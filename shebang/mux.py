# encoding: utf-8
import os, sys
import functools
import time
import json
import sublime
from collections import defaultdict
from format import Formatter
from proc import AsyncProcess, Task

class Multiplexer(object):
    formatter = Formatter()
    _frame = None
    _views = {} # output views
    _procs = {} # running threads

    def get_frame(self):
        if not self._wakeup():
            before = set([w.id() for w in sublime.windows()])
            sublime.run_command("new_window")
            after = set([w.id() for w in sublime.windows()])
            self._frame = after.difference(before).pop()
            sublime.set_timeout(functools.partial(self._cleanup, monitor=True), 1000)

        for win in sublime.windows():
            if win.id() == self._frame:
                return win

    def _wakeup(self):
        if self._frame is None or self._frame not in (w.id() for w in sublime.windows()):
            self._frame = None
            for w in sublime.windows():
                for v in w.views():
                    if v.settings().has('shebang.task_id'):
                        self._frame = w.id()
                        task_id = Task(v)
                        # task_id = json.loads(v.settings().get('shebang.task_id','[]'))
                        task_inv = json.loads(v.settings().get('shebang.invocation','{}'))
                        if task_id and task_inv:
                            # task_id = Task(*task_id)
                            task_inv['task'] = task_id
                            self._views[task_id] = v #.id()
                            self.formatter.fold_old(v)
                if self._frame: break
        return self._frame is not None                

    def _cleanup(self, monitor=False):
        if not self._wakeup():
            if self._procs:
                for info, proc in self._procs.items():
                    print 'Halted %s'%info.path
                    proc.kill()
                for task_id in self._views.keys():
                    self.formatter.clear_errors(Task(*task_id))
                self._procs = {}
                self._views = {}
                self._frame = None
        elif not self._procs:
            pass
        elif monitor:
            sublime.set_timeout(functools.partial(self._cleanup,monitor=True), 1000)
        
    def _setting(self, key):
        return sublime.load_settings('Shebang.sublime-settings').get(key)

    def script_view(self, task_id):
        match = [v for w in sublime.windows() for v in w.views() if v.id()==task_id.view]
        if match: return match[0]

    def script_win(self, task_id):
        match = [w for w in sublime.windows() for v in w.views() if v.id()==task_id.view]
        if match: return match[0]

    def output_view(self, task_id, create=False):
        if task_id in self._views:
            return self._views[task_id]

        view_id = task_id.view
        for view in (v for w in sublime.windows() for v in w.views() if v.settings().has('shebang.task_id')):
            this_task = Task(view)
            if this_task.view == view_id:
                self._views[task_id] = view
                return view

        if create:
            same_window = not self._setting('output_separate_window')
            if same_window:
                win = self.script_win(task_id) or sublime.active_window()
            else:
                pass # need something like _wakeup to deal with global window identification
                win = self.get_frame()

            view = win.new_file()
            view.set_scratch(True)
            view.set_syntax_file("Packages/Shebang/Output.tmLanguage")
            view.settings().set('word_wrap', False)
            view.settings().set("scroll_past_end", False)
            self._views[task_id] = view
            return view

    def spawn_worker(self, task_id, invocation):
        if self._procs.get(task_id):
            if not self.stop_worker(task_id):
                return

        invocation['task'] = task_id
        old_cwd = os.getcwd()
        old_path = os.environ["PATH"] 
        err_type = WindowsError if os.name=="nt" else OSError

        try:
            if invocation['path']:
                os.environ["PATH"] = os.path.expandvars(invocation["path"]).encode(sys.getfilesystemencoding())                
            os.chdir(invocation['working_dir'])
            proc = AsyncProcess(listener=self, **invocation)
            view = self.output_view(task_id, create=True)
            view.settings().set("shebang.invocation", json.dumps(invocation))
            view.settings().set("shebang.task_id", json.dumps(task_id))
            view.settings().set("shebang.task_pid", proc.pid)
            self._procs[task_id] = proc
            self.formatter.begin_run(view, proc.pid, invocation)
            print 'Running %s'%task_id.path

        except err_type as e:
            output = []
            output.append("%s\n"%e.strerror)
            output.append(" cmd: %s"%(" ".join(invocation['arg_list'])))
            output.append(' pwd: %s'%os.getcwdu())
            output.append('path: %s'%(invocation['env'].get('PATH', os.environ['PATH'])))
            
            panel = sublime.active_window().get_output_panel("shebang")
            panel.set_read_only(False)
            edit = panel.begin_edit()
            panel.insert(edit, panel.size(), "\n".join(output))
            panel.show(panel.size())
            panel.end_edit(edit)
            panel.set_read_only(True)
            sublime.active_window().run_command("show_panel", {"panel": "output.shebang"})
        finally:
            os.chdir(old_cwd)
            os.environ['PATH'] = old_path

    def stop_worker(self, task_id):
        if self._procs.get(task_id):
            if task_id.view==-1:
                name = task_id.path[:32] + u"…" if len(task_id.path)>32 else u""
            else:
                name = os.path.basename(task_id.path)

            auto_ok = not self._setting('confirm_terminate')
            if auto_ok or sublime.ok_cancel_dialog('%s:\nKill currently running process?'%name):
                stale_proc = self._procs[task_id]
                stale_proc.kill()
                self.script_complete(stale_proc)
                return True
            else:
                return False

    def view_closed(self, view):
        task_id = Task(view)            
        if task_id and task_id in self._views: 
            del self._views[task_id]

        self.formatter.clear_errors(task_id)
        stale_proc = self._procs.get(task_id)
        if stale_proc:
            print 'Halted %s'%stale_proc.task.path
            stale_proc.kill()
            del self._procs[task_id]

    def script_complete(self, proc):
        print 'Complete %s'%proc.task.path
        view = self.output_view(proc.task)

        info = json.loads(view.settings().get("shebang.invocation", '{}'))
        if info:
            info['task'] = Task(*info['task'])
            info.update(dict(exit_code=proc.exit_code(), 
                             elapsed=time.time() - proc.start_time))
            self.formatter.completed_run(view, proc.task, info)
            view.settings().erase("shebang.task_pid")
            del self._procs[proc.task]
        else:
            print "...but output window is lost"

    # event handlers for the async proc running behind the scenes
    def on_data(self, proc, data):
        sublime.set_timeout(functools.partial(self._flush, proc, data), 0)

    def _flush(self, proc, data):
        if data is None:
            return self.script_complete(proc)

        try:
            txt = data.decode(proc.encoding)
        except:
            txt = u"[Decode error - output not %s]\n"%proc.encoding
        view = self.output_view(proc.task)
        self.formatter.append_txt(view, txt)
