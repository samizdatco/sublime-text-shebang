# encoding: utf-8
import os, sys, re
import datetime
import sublime
from sublime import Region

class Formatter(object):
    # m_begin, m_output, m_result, m_end = list(u"☃☂☔☊")
    m_begin, m_output, m_result, m_end = list(u'\u200b\u200c\u200d\u2060')
    
    def begin_run(self, view, pid, invocation):

        header = []
        if view.size()==0:
            cmd = invocation['arg_list']
            cmd_str = cmd if invocation.get('shell') \
                else " ".join([('"%s"'%c if ' ' in c else c) for c in cmd])

            header.append(u" cmd: %s\n"%cmd_str)
            header.append(u" dir: %s\n"%invocation['working_dir'].replace(os.environ['HOME'],'~'))
            header.append(u"path: %s\n\n"%invocation['env'].get('PATH',os.environ['PATH']))

        self.fold_old(view)
        timestamp = (u"%s"%datetime.datetime.now()).split('.')[0].replace('-','/')
        header.append(u"%s%s [%i]%s\n\n"%(self.m_begin,timestamp,pid,self.m_output))
        self.append_txt(view, u"".join(header))
        view.set_status("shebang:running",'Running')

    def append_txt(self, view, txt):
        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        txt = txt.replace('\r\n', '\n').replace('\r', '\n')
        selection_was_at_end = (len(view.sel()) == 1
            and view.sel()[0] == Region(view.size()))
        view.set_read_only(False)
        edit = view.begin_edit()
        view.insert(edit, view.size(), txt)
        if selection_was_at_end:
            view.show(view.size())
        view.end_edit(edit)
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

    def completed_run(self, view, task_id, exit_code, elapsed):
        view.set_read_only(False)

        begin = view.find_by_selector('comment.header.shebang')[-1]
        errstr = " %i"%exit_code if exit_code else ''
        run_body = view.substr(Region(begin.b+3, view.size()))

        sizestr = self._pretty('size',view.size()-begin.b-3)
        timestr = self._pretty('time', elapsed)
        
        edit = view.begin_edit()
        view.insert(edit, view.size(), u'\n%s%s %s %s%s\n'%(self.m_result,timestr,sizestr,errstr, self.m_end))
        for r in view.find_by_selector('keyword.pid.shebang'):
            view.erase(edit, Region(r.a-2, r.b+1))
        view.show(view.size())
        view.end_edit(edit)
        view.set_read_only(True)
        view.erase_status("shebang:running")

        if exit_code:
            parent_win = [w for w in sublime.windows() if w.id()==task_id.window]
            if not parent_win: return

            active_win = parent_win[0]
            parent_view = [v for v in active_win.views() if v.id()==task_id.view]
            if parent_view: parent_view = parent_view[0]

            panel = active_win.get_output_panel("shebang")
            panel.set_read_only(False)
            edit = panel.begin_edit()
            panel.insert(edit, panel.size(), run_body)
            panel.show(panel.size())
            panel.end_edit(edit)
            panel.set_read_only(True)
            active_win.run_command("show_panel", {"panel": "output.shebang"})

            for fn, lineno in reversed(re.findall(r'^[ ]*File \"(...*?)\", line ([0-9]*)', run_body, re.M)):
                if parent_view and task_id.path.endswith(fn):
                    errline = parent_view.split_by_newlines(Region(0,parent_view.size()))[int(lineno)-1]
                    parent_view.sel().clear()
                    parent_view.sel().add(Region(errline.b,errline.b))
                    break
