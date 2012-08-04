# encoding: utf-8
import os, sys
import functools
import time
import json
import sublime
from format import Formatter
from proc import AsyncProcess, Task

class Multiplexer(object):
    formatter = Formatter()
    _frame = None
    _views = {} # output views in _frame
    _procs = {} # running threads
    _invs = {}  # cmd invocations

    def get_frame(self):
        if self._frame is None or self._frame not in (w.id() for w in sublime.windows()):
            before = set([w.id() for w in sublime.windows()])
            sublime.run_command("new_window")
            after = set([w.id() for w in sublime.windows()])
            self._frame = after.difference(before).pop()
            sublime.set_timeout(functools.partial(self._cleanup, monitor=True), 1000)

        for win in sublime.windows():
            if win.id() == self._frame:
                return win

    def _cleanup(self, monitor=False):
        if self._frame is None or self._frame not in (w.id() for w in sublime.windows()):
            if self._procs:
                for info, proc in self._procs.items():
                    print 'Halted %s'%info.path
                    proc.kill()
                self._procs = {}
                self._views = {}
                self._invs = {}
                self._frame = None
        elif not self._procs:
            pass
        elif monitor:
            sublime.set_timeout(functools.partial(self._cleanup,monitor=True), 1000)

    def get_task_view(self, task_id):
        win = self.get_frame()
        if task_id in self._views:
            view_id = self._views[task_id]
            for view in win.views():
                if view.id()==view_id:
                    return view
                    
        view = win.new_file()
        if task_id.view != -1:
            view.set_name('%s (%s)'%(os.path.basename(task_id.path), os.path.dirname(task_id.path).replace(os.environ.get('HOME',''),'~')))
        else:
            view.set_name(task_id.path[:256])
        view.set_scratch(True)
        view.set_syntax_file("Packages/Shebang/Shebang.tmLanguage")
        view.settings().set('word_wrap', False)
        # view.settings().set("result_file_regex", file_regex)
        # view.settings().set("result_line_regex", line_regex)
        # view.settings().set("result_base_dir", working_dir)
        self._views[task_id] = view.id()
        return view

    def spawn_worker(self, task_id, invocation=None):
        if self._procs.get(task_id):
            if not self.stop_worker(task_id):
                return

        if invocation:
            invocation['listener'] = self
            invocation['task'] = task_id
            self._invs[task_id] = invocation
        else:
            invocation = self._invs.get(task_id)
            if not invocation:
                print "Trying to rerun script but i don't remember where i came from"
                return

        old_cwd = os.getcwd()
        old_path = os.environ["PATH"] 
        err_type = WindowsError if os.name=="nt" else OSError

        try:
            if invocation['path']:
                os.environ["PATH"] = os.path.expandvars(invocation["path"]).encode(sys.getfilesystemencoding())                
            os.chdir(invocation['working_dir'])
            proc = AsyncProcess(**invocation)
            view = self.get_task_view(task_id)
            view.settings().set("task_id", json.dumps(task_id))
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

            auto_ok = not sublime.load_settings('Shebang.sublime-settings').get('confirm_terminate')
            if auto_ok or sublime.ok_cancel_dialog('%s:\nKill currently running process?'%name):
                stale_proc = self._procs[task_id]
                stale_proc.kill()
                self.script_complete(stale_proc)
                return True
            else:
                return False

    def view_closed(self, task_id):
        if task_id in self._views: del self._views[task_id]
        if task_id in self._invs: del self._invs[task_id]
        stale_proc = self._procs.get(task_id)
        if stale_proc:
            print 'Halted %s'%stale_proc.task.path
            stale_proc.kill()
            del self._procs[task_id]

    def script_complete(self, proc):
        print 'Complete %s'%proc.task.path
        view = self.get_task_view(proc.task)
        elapsed = time.time() - proc.start_time
        exit_code = proc.exit_code()
        self.formatter.completed_run(view, proc.task, exit_code, elapsed)
        del self._procs[proc.task]

    # event handlers for the async proc running behind the scenes
    def on_data(self, proc, data):
        try:
            txt = data.decode(proc.encoding)
        except:
            txt = u"[Decode error - output not %s]\n"%proc.encoding
        sublime.set_timeout(functools.partial(self._flush, proc, txt), 0)

    def on_finished(self, proc):
        sublime.set_timeout(functools.partial(self.script_complete, proc), 50)

    def _flush(self, proc, txt):
        view = self.get_task_view(proc.task)
        self.formatter.append_txt(view, txt)
