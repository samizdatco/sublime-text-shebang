# encoding: utf-8
import os, sys
import thread
import subprocess
import time
import json
from sublime import View
from operator import itemgetter
    
# used as a token to uniquely identify tasks and source view
class Task(tuple):
    def __new__(_cls, *args):
        if not args or args[0] is None: 
            return None

        if isinstance(args[0], View):
            raw_task = args[0].settings().get('shebang.task_id')
            return tuple.__new__(_cls, json.loads(raw_task)) if raw_task else None
        elif len(args)==2:
            return tuple.__new__(_cls, (args[0], int(args[1])))
        elif len(args)==1:
            return tuple.__new__(_cls, (args[0], -1))
    path = property(itemgetter(0))
    view = property(itemgetter(1))


# subprocess.Popen with a threaded listener (from Default/exec.py)
class AsyncProcess(object):
    def __init__(self, arg_list, env, listener,
                shell=False, encoding=None, task=None, 
                **kwargs):
        self.inv = dict((k,v) for k,v in locals().items() if k not in ['self','listener'])
        self.listener = listener
        self.killed = False
        self.ttl = 1 # 2 (i guess there's nothing to be lost by merging stdout+err?)
        self.encoding = encoding
        self.task = task
        self.start_time = time.time()

        # Hide the console window on Windows
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc_env = os.environ.copy()
        proc_env.update(env)
        for k, v in proc_env.iteritems():
            proc_env[k] = os.path.expandvars(v).encode(sys.getfilesystemencoding())

        self.proc = subprocess.Popen(arg_list, stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=shell)
            stderr=subprocess.STDOUT, startupinfo=startupinfo, env=proc_env, shell=shell)

        if self.proc.stdout:
            thread.start_new_thread(self.read_stdout, ())

        if self.proc.stderr:
            thread.start_new_thread(self.read_stderr, ())

        self.pid = self.proc.pid

    def kill(self):
        if not self.killed:
            self.killed = True
            self.proc.terminate()
            self.listener = None

    def poll(self):
        return self.proc.poll() == None

    def exit_code(self):
        return self.proc.poll()

    def read_stdout(self):
        while True:
            data = os.read(self.proc.stdout.fileno(), 2**15)

            if data != "":
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                self.proc.stdout.close()
                if self.listener:
                    self.listener.on_data(self, None)
                break

    def read_stderr(self):
        while True:
            data = os.read(self.proc.stderr.fileno(), 2**15)

            if data != "":
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                self.proc.stderr.close()
                if self.listener:
                    self.listener.on_data(self, None)
                break
