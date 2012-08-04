# encoding: utf-8
import os, sys
import thread
import subprocess
import time
from operator import itemgetter
    
# used as a token to uniquely identify tasks and source view
class Task(tuple):
    def __new__(_cls, path, window, view):
        return tuple.__new__(_cls, (path, window, view)) 
    path = property(itemgetter(0))
    window = property(itemgetter(1))
    view = property(itemgetter(2))

# subprocess.Popen with a threaded listener (from Default/exec.py)
class AsyncProcess(object):
    def __init__(self, arg_list, env, listener,
                shell=False, encoding=None, task=None, 
                working_dir=None, path=None):

        self.listener = listener
        self.killed = False
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
            stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=shell)

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
                    self.listener.on_finished(self)
                break

    def read_stderr(self):
        while True:
            data = os.read(self.proc.stderr.fileno(), 2**15)

            if data != "":
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                self.proc.stderr.close()
                break

