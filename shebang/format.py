# encoding: utf-8
import os, sys, re
import datetime
import sublime
import functools
import time
from collections import defaultdict
from os.path import join, exists, normpath, relpath, basename, dirname
from itertools import izip_longest as izipl
from sublime import Region
from proc import Task

class Formatter(object):
    # m_begin, m_output, m_result, m_end = list(u"☃☂☔☊")
    m_begin, m_output, m_result, m_end = list(u'\u200b\u200c\u200d\u2060')
    _err = {} # view ids with errorline info
    
    def begin_run(self, view, pid, inv):
        header = []
        if view.size()==0:
            cmd = inv['arg_list']
            cmd_str = cmd if inv.get('shell') \
                else " ".join([('"%s"'%c if ' ' in c else c) for c in cmd])

            header.append(u" cmd: %s\n"%cmd_str)
            header.append(u" dir: %s\n"%inv['working_dir'].replace(os.environ['HOME'],'~'))
            header.append(u"path: %s\n\n"%inv['env'].get('PATH',os.environ['PATH']))

        self.fold_old(view)

        status = inv['arg_list'] if inv.get('shell') else basename(inv['task'].path)
        view.set_name(u'… %s'%status)

        timestamp = (u"%s"%datetime.datetime.now()).split('.')[0].replace('-','/')
        header.append(u"%s%s [%i]%s\n\n"%(self.m_begin,timestamp,pid,self.m_output))
        self.append_txt(view, u"".join(header))
        view.set_status("shebang:running",'Running')

    def append_txt(self, view, txt):
        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        txt = txt.replace('\r\n', '\n').replace('\r', '\n')
        
        # todo: should also only bother scrolling if the new end is beyond 
        # the view bounds
        selection_was_at_end = (len(view.sel()) == 1
            and view.sel()[0] == Region(view.size()))
        # </todo>

        view.set_read_only(False)
        edit = view.begin_edit()
        view.insert(edit, view.size(), txt)
        view.end_edit(edit)
        if selection_was_at_end:
            view.run_command("move_to", {"to": "eof", "extend": False} )
        view.set_read_only(True)

    def _pretty(self, kind, val):
        if kind=='time':
            hrs = val // 3600 
            val = val - (hrs * 3600)
            mins = val // 60
            secs = val - (mins * 60)            
            if hrs:
                return '%ih%i\'%1.1f"' % (hrs, mins, secs)
            else:
                return '%i\'%1.1f"' % (mins, secs)
        elif kind=='size':
            if val==1: return "1 byte"
            sfix = 'bytes|kb|mb|gb'.split('|')
            while sfix[1:] and val>=1024:
                val /= 1024.0
                sfix.pop(0)
            if len(sfix)>2:
                val = int(val)
            frac = ('%1.1f'%val).replace('.0','')
            return '%s %s'%(frac,sfix[0])
            
    def fold_old(self, view):
        view.fold(view.find_by_selector('output.shebang'))


    def completed_run(self, view, task_id, info):
        exit_code = info['exit_code']
        elapsed = info['elapsed']

        begin = view.find_by_selector('comment.header.shebang')[-1]
        run_body = view.substr(Region(begin.b+3, view.size()))

        # write the post-run footer with time, bytes, and exit code
        errstr = ''
        if 0 < exit_code < 11:
            errstr = u"⓵⓶⓷⓸⓹⓺⓻⓼⓽⓾"[exit_code-1]
        elif exit_code:
            errstr = str(exit_code)
        sizestr = self._pretty('size',view.size()-begin.b-3)
        timestr = self._pretty('time', elapsed)
        self.append_txt(view, u'\n%s%s %s %s%s\n'%(self.m_result,timestr,sizestr,errstr, self.m_end))

        # clip out the pid from the pre-run header
        view.set_read_only(False)
        edit = view.begin_edit()
        # view.insert(edit, view.size(), u'\n%s%s %s %s%s\n'%(self.m_result,timestr,sizestr,errstr, self.m_end))
        for r in view.find_by_selector('keyword.pid.shebang'):
            view.erase(edit, Region(r.a-2, r.b+1))
        view.end_edit(edit)
        view.set_read_only(True)
        view.erase_status("shebang:running")


        # if exit wasn't clean, 
        status = info['arg_list'] if info.get('shell') else basename(info['task'].path)
        if not exit_code:
            # yay, no errors
            view.set_name(u'✓ %s'%(status))
            self.clear_errors(task_id)
        else:
            view.set_name(u'%s %s'%(errstr, status))

            # examine the output for a recognizable traceback (for now just python...)
            stack_frames, err_body = self._detect_stacktrace(task_id, info, run_body)
            self._display_stacktrace(task_id, info, err_body)

            err_paths = [f['path'] for f in stack_frames]
            err_gen = "%x"%hash(time.time())

            self._err[task_id] = dict(stack=stack_frames, 
                                      gen=err_gen, 
                                      cwd=info['working_dir'] )

            for win, view in ((w,v) for w in sublime.windows() for v in w.views() if v.file_name() in err_paths):
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
                    self.flash_errors(win.active_view())

    def _display_stacktrace(self, task_id, inv, err_body):
        try:
            parent_win = [w for w in sublime.windows() for v in w.views() if v.id()==task_id.view][0]
        except IndexError:
            print "source script no longer open..."
            return

        # only show the error panel if we're not looking at the output buffer
        if sublime.active_window().active_view().settings().has('shebang.task_id'):
            parent_win.run_command("hide_panel", {"panel": "output.shebang"})
        elif err_body:
            panel = parent_win.get_output_panel("shebang")
            panel.settings().set("result_file_regex", inv['file_regex'])
            panel.settings().set("result_line_regex", inv['line_regex'])
            panel.settings().set("result_base_dir", inv['working_dir'])
            panel = parent_win.get_output_panel("shebang")
            panel.set_read_only(False)
            edit = panel.begin_edit()
            panel.insert(edit, panel.size(), err_body)
            panel.show(panel.size())
            panel.end_edit(edit)
            panel.set_read_only(True)
            parent_win.run_command("show_panel", {"panel": "output.shebang"})

    def _detect_stacktrace(self, task_id, inv, run_body):
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
        
    def clear_errors(self, task_id):
        if task_id in self._err:
            # print "clear", task_id, self._err.get(task_id,{}).get('gen')
            del self._err[task_id] 

    def flash_errors(self, view):
        if not self.has_errors(view): return

        view.erase_regions('shebang.mark')
        err = view.settings().get('shebang.stacktrace')
        goto = view.settings().get('shebang.goto')
        if goto is not None:
            err['depth'] = goto
            view.settings().set('shebang.stacktrace', err)
            lineno = int(err['stack'][int(err['depth'])])
            view.window().open_file("%s:%i"%(view.file_name(), lineno), sublime.ENCODED_POSITION)

        def blinkenlights(ttl=4):
            if ttl%2:
                view.add_regions('shebang.mark', [blinkenlights.errline], 'comment', '', sublime.DRAW_OUTLINED)    
            else:
                view.add_regions('shebang.mark', [blinkenlights.errline], 'comment', '', sublime.HIDDEN)    
            if ttl:
                sublime.set_timeout(functools.partial(blinkenlights,ttl-1), 90)
        lineno = int(err['stack'][int(err['depth'])])
        blinkenlights.errline = view.split_by_newlines(Region(0,view.size()))[lineno-1]
        sublime.set_timeout(functools.partial(blinkenlights), 90)

        if goto is not None:
            view.show_at_center(blinkenlights.errline)
            view.settings().erase('shebang.goto')

    def has_errors(self, view):
        task_id = Task(view)
        if task_id:
            return task_id in self._err

        trace = view.settings().get('shebang.stacktrace', {})
        trace_gen = trace.get('gen')
        task_id = Task(*trace.get('task',[None]))
        if trace and task_id in self._err:
            if trace_gen == self._err[task_id]['gen']:
                return True

        view.settings().erase('shebang.stacktrace')
        view.settings().erase('shebang.goto')
        view.erase_regions('shebang.mark')
        return False

    def browse_stacktrace(self, task_id):
        err = self._err.get(task_id)
        if err:
            file_paths = []
            ui = []

            # from pprint import pprint
            # pprint(err)

            for frame in err['stack']:
                pth = frame['path']
                file_paths.append('%s:%i'%(pth, frame['line']))
                rel_pth = relpath(pth, err['cwd'])
                if rel_pth.startswith('..'):
                    home_pth = re.sub(r'^'+os.environ['HOME'], u'~', pth)
                    if len(home_pth) < len(rel_pth):
                        rel_pth = home_pth
                if len(pth) < len(rel_pth):
                    rel_pth = pth

                if len(rel_pth)>48:
                    rel_pth = u"%s…%s"%(rel_pth[:24], rel_pth[-24:])

                ctx = frame.get('context')
                if ctx:
                    # ui.append(["%s: %i"%(rel_pth,frame['line']), ctx])
                    ui.append(["%s: %s"%(rel_pth,ctx['fn']), ctx['src']])
                else:
                    ui.append("%s: %i"%(rel_pth,frame['line']))

            def outcome(idx):
                if idx>=0:
                    file_path = err['stack'][idx]['path']
                    lineno = err['stack'][idx]['line']
                    for win in (w for w in sublime.windows() for v in w.views() if v.id()==task_id.view):
                        match = [v for v in win.views() if v.file_name()==file_path]
                        if match:
                            view = match[0]
                            view.settings().set('shebang.goto',idx)
                            if view.id()==sublime.active_window().active_view().id():
                                self.flash_errors(view)
                            else:
                                win.open_file(file_path)
                            return

                    view = sublime.active_window().open_file(file_path)
                    stack = [ (f['path']==file_path and f['line']) for f in err['stack']]
                    view.settings().set('shebang.goto', idx)
                    view.settings().set('shebang.stacktrace', {"task":[task_id.path, task_id.view], 
                                                               "gen":err['gen'],
                                                               "stack":stack, 
                                                               "depth":idx})
            sublime.active_window().show_quick_panel(ui, outcome)
