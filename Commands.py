# encoding: utf-8
from __future__ import division
import os, sys
import functools
import json
import shlex
import sublime, sublime_plugin
from sublime import Region
from os.path import dirname, relpath, exists

from shebang import Task, AsyncProcess, Formatter, Multiplexer
# import shebang.format
# reload(shebang.format); Formatter = shebang.format.Formatter
# import shebang.proc
# reload(shebang.proc); Task = shebang.proc.Task; AsyncProcess = shebang.proc.AsyncProcess
# import shebang.mux
# reload(shebang.mux); Multiplexer = shebang.mux.Multiplexer

pool = Multiplexer()
class OutputViewWatcher(sublime_plugin.EventListener):
    def on_close(self, view):
        pool.view_closed(view)

    def on_load(self, view):
        self._check_for_errors(view)

    def on_activated(self, view):
        self._check_for_errors(view)

    def _check_for_errors(self, view):
        if view.settings().has('shebang.stacktrace') and pool.has_stacktrace(view):
            pool.formatter.flash_errors(view)

class LastStackTraceCommand(sublime_plugin.WindowCommand):
    def run(self, *args, **kwargs):
        view = self.window.active_view()
        task_id = Task(view)

        if not task_id:
            task_id = Task(*view.settings().get('shebang.stacktrace',{}).get('task',[]))

        if task_id:
            pool.browse_stacktrace(task_id)
            
    def is_enabled(self):
        return pool.has_stacktrace(self.window.active_view())
        

class ExecuteCommand(sublime_plugin.WindowCommand):
    def run(self, cmd = None, file_regex = "", line_regex = "", working_dir = "",
            encoding = "utf-8", env = {}, quiet = False, kill = False, 
            virtualenv=None, prompt=False, path=None, restart=False, **kwargs):

        # if invoked from an output buffer, use the cached invocation rather than
        # treating the output buffer's contents as a script to be run
        if self._cached_run(prompt, kill): 
            return

        # if invoked from a script file, collect the subprocess invocation 
        # details into a cacheable dict (for future reruns)
        view = self.window.active_view()
        file_path = view.file_name()
        task_id = Task(file_path, view.id())
        if not working_dir:
            try:
                working_dir = dirname(file_path)
            except:
                working_dir = os.environ('HOME')
        file_name = relpath(file_path, working_dir)
        invocation=dict(arg_list=None, working_dir=working_dir, 
                        env=env.copy(), encoding=encoding, path=path,
                        file_regex=file_regex, line_regex=line_regex)
        invocation['env'].update(view.settings().get('build_env',{}))
        invocation.update(kwargs)
        
        # catch ctrl-c
        if kill: return pool.stop_worker(task_id)
            
        # process the command param or create one from the file's shebang line
        if cmd is None:
            cmd = []
            shebang = view.substr(view.line(0))
            if shebang.startswith('#!'):
                cmd = shlex.split(shebang[2:].encode('utf-8')) + [file_name]

        # special handling if python is involved
        if file_path.endswith('.py') or 'python' in str(cmd):
            if not invocation['file_regex']:
                invocation['file_regex'] =  "^[ ]*File \"(...*?)\", line ([0-9]*)"
            ve_python = self._closest_virtualenv(file_path, virtualenv)
            if ve_python:
                cmd = [relpath(ve_python, working_dir), '-u', file_name]

            # default to the system path if no virtualenv was found and cmd wasn't 
            # in the build system cfg
            if not cmd:
                cmd = ['/usr/bin/env','python','-u', file_name]

            # turn off python's stdout buffering 
            if 'python' in str(cmd) and '-u' not in cmd:
                cmd.insert(-1,'-u')

        invocation['arg_list'] = cmd
        if pool._setting('save_on_run'):
            if view.is_dirty(): 
                view.run_command('save')

        if prompt:
            self._prompt_then_run(task_id, invocation)
        else:
            pool.spawn_worker(task_id, invocation)

    def _cached_run(self, prompt, kill):
        view = self.window.active_view()
        task_id = Task(view)
        task_inv = json.loads(view.settings().get("shebang.invocation", '{}'))
        if task_id and task_inv:
            if prompt: 
                self._prompt_then_run(task_id, task_inv)
            elif kill: 
                pool.stop_worker(task_id)
            else: 
                pool.spawn_worker(task_id, task_inv)
            return True

    def _prompt_then_run(self, task_id, invocation):
        orig_cmd = invocation['arg_list']
        if isinstance(orig_cmd, (list,tuple)):
            orig_cmd = " ".join([('"%s"'%c if ' ' in c else c) for c in orig_cmd])

        def _spawn(cmd_str):
            cmd_str = cmd_str.strip()
            if cmd_str != _spawn.quoted:
                _spawn.inv.update(dict(arg_list=cmd_str, shell=True))
                pool.spawn_worker(Task(cmd_str, -1), _spawn.inv)
            else:
                pool.spawn_worker(_spawn.task, _spawn.inv)
        _spawn.quoted = orig_cmd
        _spawn.task = task_id
        _spawn.inv = invocation

        prompt_str = invocation['working_dir'].replace(os.environ['HOME'],'~')
        if len(prompt_str)>48:
            prompt_str = u"%s…%s"%(prompt_str[:24], prompt_str[-24:])
        sublime.active_window().show_input_panel("%s %%"%prompt_str, _spawn.quoted, _spawn, None, None)

    def _closest_virtualenv(self, file_path, ve_pattern):
        if not ve_pattern:
            # use the virtualenv defined in the settings file but let anything
            # defined in a build setting override it
            ve_pattern = pool._setting('virtualenv')
            if not ve_pattern: return None

        ve_binary = os.path.join(ve_pattern,'bin','python')
        if ve_binary[0] in '~/':
            # try using the virtualenv config string as an absolute path...
            ve_pth = ve_binary.replace('~',os.environ.get('HOME','~'))
            if exists(ve_pth):
                return ve_pth
        else:
            # ...otherwise keep stepping up from the file_path's dir looking 
            # for a folder with a name matching the config string
            for ve_pth in self._parents(file_path, ve_binary):
                if exists(ve_pth):
                    return ve_pth

    def _parents(self, file_path, sub_path=None):
        dirs = []
        parent_dir = dirname(file_path)
        while True:
            if sub_path: dirs.append(os.path.join(parent_dir, sub_path))
            else: dirs.append(parent_dir)
            new_dir = dirname(parent_dir)
            if parent_dir == new_dir: break
            parent_dir = new_dir
        return dirs

    def is_enabled(self, restart=False, kill=False, prompt=False, *args, **kwargs):
        if prompt: return True
        view = self.window.active_view()
        fname = view.file_name()
        task_id = Task(view)
        if not task_id:
            task_id = (fname, self.window.id(), view.id())
        is_running = task_id and Task(*task_id) in pool._procs

        if kill or restart:
            return is_running
            
        if task_id \
        or fname.lower().endswith('.py') \
        or view.substr(Region(0,2))=="#!":
            return not is_running
