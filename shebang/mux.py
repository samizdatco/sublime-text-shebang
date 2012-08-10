# encoding: utf-8
import os, sys, re
import functools
import time
import json
import signal
from collections import defaultdict
from itertools import izip_longest as izipl
from os.path import join, exists, basename

import sublime
from sublime import Region
from format import Formatter
from proc import AsyncProcess, Task
all_views = lambda: ((w,v) for w in sublime.windows() for v in w.views())

class Multiplexer(object):
    formatter = Formatter()
    _awake = False
    _frame = None
    _views = {} # output views
    _procs = {} # running threads
    _stacks = {} # stack traces
    
    def __init__(self):
        def destroy_all_zombies(ttl=20):
            loaded = [not v.is_loading() for w in sublime.windows() for v in w.views()]
            if not (loaded and all(loaded)):
                # keep waiting for the views to load 
                if ttl: sublime.set_timeout(functools.partial(destroy_all_zombies, ttl-1), 100)
            else:
                renamed = {}
                for _,view in all_views():
                    task_id = Task(view)
                    src_id = json.loads(view.settings().get('shebang.src_id','[]'))
                    pid = view.settings().get('shebang.task_pid')
                    if task_id and pid:
                        # kill any processes that are still running since the last editor launch
                        print "Zombie process (%i): %s"%(pid, task_id.path)
                        view.settings().erase('shebang.task_pid')
                        os.kill(pid, signal.SIGKILL)
                        task_inv = json.loads(view.settings().get("shebang.invocation", '{}'))
                        self.formatter.zombie_quit(view, task_id, task_inv)
                    elif task_id:
                        # tidy left over output views
                        self.formatter.fold_prior_output(view)
                    elif src_id:
                        # note any src scripts whose view id has changed
                        src_file, src_view = src_id
                        if view.id() != src_view:
                            renamed[Task(src_file, src_view)] = json.dumps([src_file, view.id()])
                
                # update the task_id in any output window corresponding to a view-shifted src
                if renamed:
                    for _,view in all_views():
                        task_id = Task(view)
                        if task_id in renamed:
                            view.settings().set('shebang.task_id', renamed[task_id])

                # kick off the watchdog process that catches processes whose window got deleted
                self._watch()            
        destroy_all_zombies()

    def _watch(self, dt=7000):
        if self._procs: 
            out_views = set([v.id() for v in self._views.values()])
            live_views = set([v.id() for w,v in all_views()])
            gone_views = out_views.difference(live_views)
            dt = 1000

            if gone_views:
                gone_tasks = [task_id for task_id,v in self._views.items() if v.id() in gone_views]
                for task_id, proc in list(self._procs.items()):
                    if task_id in gone_tasks:
                        print "Orphaned process (%i): %s"%(proc.pid, task_id.path)
                        proc.kill()
                        del self._procs[task_id]
                        del self._views[task_id]
        sublime.set_timeout(functools.partial(self._watch), dt)

    def _setting(self, key):
        return sublime.load_settings('Shebang.sublime-settings').get(key)

    def script_win(self, task_id):
        match = [w for w,v in all_views() if v.id()==task_id.view]
        if match: return match[0]

    def script_view(self, task_id):
        match = [v for w,v in all_views() if v.id()==task_id.view]
        if match: return match[0]

    def output_win(self):
        if self._frame is None or self._frame not in (w.id() for w in sublime.windows()):
            before = set([w.id() for w in sublime.windows()])
            sublime.run_command("new_window")
            after = set([w.id() for w in sublime.windows()])
            self._frame = after.difference(before).pop()

        for win in sublime.windows():
            if win.id() == self._frame:
                return win

    def output_view(self, task_id, create=False):
        old_view = self._views.get(task_id)            
        if old_view:
            if old_view.id() in [v.id() for w,v in all_views()]:
                return old_view
            del self._views[task_id]

        view_id = task_id.view
        for view in (v for w,v in all_views() if v.settings().has('shebang.task_id')):
            this_task = Task(view)
            if this_task.view == view_id:
                self._views[task_id] = view
                return view

        if create:
            same_window = not self._setting('use_separate_window')
            if same_window:
                win = self.script_win(task_id) or sublime.active_window()
            else:
                win = self.output_win()
                pass # need something like _wakeup to deal with global window identification

            view = win.new_file()
            view.set_scratch(True)
            view.set_syntax_file("Packages/Shebang/Output.tmLanguage")
            view.settings().set('word_wrap', False)
            view.settings().set("scroll_past_end", False)
            self._views[task_id] = view
            return view

    def view_closed(self, view):
        task_id = Task(view)            
        if task_id and task_id in self._views: 
            del self._views[task_id]

        if task_id in self._stacks:
            del self._stacks[task_id] 

        stale_proc = self._procs.get(task_id)
        if stale_proc:
            print 'Halted %s'%stale_proc.task.path
            stale_proc.kill()
            del self._procs[task_id]

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
            if not invocation.get('shell'):
                src_settings = self.script_view(task_id).settings()
                src_settings.set('shebang.src_id', json.dumps(task_id))

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
                name = basename(task_id.path)

            auto_ok = not self._setting('confirm_terminate')
            if auto_ok or sublime.ok_cancel_dialog('%s:\nKill currently running process?'%name):
                stale_proc = self._procs[task_id]
                stale_proc.kill()
                self.finish_worker(stale_proc)
                return True
            else:
                return False

    def finish_worker(self, proc):
        print 'Complete %s'%proc.task.path
        view = self.output_view(proc.task)
        if not view:
            print "Orphan still writing:",proc.task.path
            return
        view.settings().erase("shebang.task_pid")

        info = json.loads(view.settings().get("shebang.invocation", '{}'))
        if info:
            task_id = info['task'] = Task(*info['task'])

            info.update(dict(exit_code=proc.exit_code(), 
                             elapsed=time.time() - proc.start_time))

            begin = view.find_by_selector('comment.header.shebang')[-1]
            run_body = view.substr(Region(begin.b+3, view.size()))

            self.formatter.completed_run(view, proc.task, info, run_body)

            if not info['exit_code']:
                if task_id in self._stacks:
                    del self._stacks[task_id] 
            else:
                # examine the output for a recognizable traceback (for now just python...)
                stack_frames, err_body = self._parse_stacktrace(run_body, info)

                # show the output panel if we're looking at a source view
                self.formatter.display_stacktrace_panel(err_body, info)

                err_paths = [f['path'] for f in stack_frames]
                err_gen = "%x"%hash(time.time())
                self._stacks[task_id] = dict(stack=stack_frames, 
                                             gen=err_gen, 
                                             cwd=info['working_dir'] )

                for win, view in ((w,v) for w,v in all_views() if v.file_name() in err_paths):
                    file_path = view.file_name()
                    stack = [ (f['path']==file_path and f['line']) for f in stack_frames]
                    for lineno in reversed(stack):
                        if lineno is not False:
                            depth = stack.index(lineno)
                            view.settings().set('shebang.goto', depth)
                            break
                    view.settings().set('shebang.stacktrace', {"task":[task_id.path, task_id.view], 
                                                               "gen":err_gen,
                                                               "stack":stack, 
                                                               "depth":depth})
                
                for win in sublime.windows():
                    if win.active_view().file_name() in err_paths:
                        self.formatter.flash_errors(win.active_view())

            if proc.task in self._procs:
                del self._procs[proc.task]
        else:
            print "...but output window is lost"

    def _parse_stacktrace(self, run_body, inv):
        re_file = re.compile(inv['file_regex'], re.M)
        m = re_file.search(run_body)
        if not m: return None, None
        err_body = run_body[m.start():]

        # at a minimum find the filepaths and linenos for each frame of the trace
        stack_frames = []
        for m in re_file.finditer(err_body):
            fn = m.group(1)
            file_path = join(inv['working_dir'], fn)
            if not exists(file_path) and exists(fn):
                file_path = fn
            stack_frames.append(dict(start=m.start(), end=m.end(), path=file_path, line=int(m.group(2))))

        # cheat a bit for python and include the echo'd source line for each frame
        for first, next in izipl(stack_frames, stack_frames[1:]):
            if next:
                rng = Region(first['end'], next['start'])
            else:
                rng = Region(first['end'], len(err_body))

            m = re.search(r'in ([^\n]*)\n([^\n]+)\n', err_body[rng.a:rng.b], re.S)
            if m:
                first['context'] = dict(fn="%s%s"%(m.group(1).strip(), 
                                           "" if m.group(1).endswith('>') else "()"),
                                        src=m.group(2).strip() )
            del first['start']
            del first['end']
        return stack_frames, err_body

    def browse_stacktrace(self, task_id):
        stacktrace = self._stacks.get(task_id)
        if stacktrace:
            self.formatter.display_stacktrace_menu(task_id, stacktrace)

    def has_stacktrace(self, view):
        task_id = Task(view)
        if task_id:
            return task_id in self._stacks

        trace = view.settings().get('shebang.stacktrace', {})
        trace_gen = trace.get('gen')
        task_id = Task(*trace.get('task',[None]))
        if trace and task_id in self._stacks:
            if trace_gen == self._stacks[task_id]['gen']:
                return True

        view.settings().erase('shebang.stacktrace')
        view.settings().erase('shebang.goto')
        view.erase_regions('shebang.mark')
        view.erase_regions('shebang.errlines')
        return False

    # event handlers for the async proc running behind the scenes
    def on_data(self, proc, data):
        sublime.set_timeout(functools.partial(self._flush, proc, data), 0)

    def _flush(self, proc, data):
        if data is None:
            proc.ttl -= 1
            if not proc.ttl:
                self.finish_worker(proc)
            return 

        try:
            txt = data.decode(proc.encoding)
        except:
            txt = u"[Decode error - output not %s]\n"%proc.encoding
        view = self.output_view(proc.task)
        self.formatter.append_txt(view, txt)
