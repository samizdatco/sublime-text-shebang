# encoding: utf-8
import os, sys, re
import datetime
import sublime
import functools
from collections import defaultdict
from os.path import join, exists, normpath
from sublime import Region

class Formatter(object):
    # m_begin, m_output, m_result, m_end = list(u"☃☂☔☊")
    m_begin, m_output, m_result, m_end = list(u'\u200b\u200c\u200d\u2060')
    _err = {}   # view ids with errorline info
    
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

    def completed_run(self, view, task_id, exit_code, elapsed, cwd):
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
            try:
                parent_win = [w for w in sublime.windows() if w.id()==task_id.window][0]
            except IndexError:
                return

            views = dict([(v.file_name(), v) for v in parent_win.views()])
            re_file = re.compile(r'^[ ]*File \"(...*?)\", line ([0-9]*)', re.M)

            panel = parent_win.get_output_panel("shebang")
            panel.set_read_only(False)
            edit = panel.begin_edit()
            panel.insert(edit, panel.size(), run_body)
            panel.show(panel.size())
            panel.end_edit(edit)
            panel.set_read_only(True)
            parent_win.run_command("show_panel", {"panel": "output.shebang"})

            err_idx = defaultdict(list) # {w_id:[v1,v2,v3], ...}
            err_rgns = defaultdict(list) # {v_obj; [[a,b], [c,d], ...]}
            for fn, lineno in reversed(re_file.findall(run_body, re.M)):
                file_path = join(cwd, fn)
                if not exists(file_path) and exists(fn):
                    file_path = fn

                parent_view = views.get(file_path)
                if parent_view:
                    err_idx[parent_win.id()].append(parent_view.id())
                    errline = parent_view.split_by_newlines(Region(0,parent_view.size()))[int(lineno)-1]
                    err_rgns[parent_view].append([errline.a, errline.b])

            for err_view, errs in err_rgns.items():
                err_view.settings().set('shebang.errorline', errs)
                err_view.settings().set('shebang.hop',"hop")
            if err_idx:
                self._err[task_id] = dict(err_idx)
            elif task_id in self._err:
                del self._err[task_id]

            active_view = sublime.active_window().active_view()
            if active_view.id() in err_idx[active_view.window().id()]:
                self.flash_errors(active_view, focus=True)
        
        elif task_id in self._err:
            del self._err[task_id] # yay, no errors


    def flash_errors(self, view, focus=False):
        err_free = True
        win_id, view_id = view.window().id(), view.id()
        for task_id, err_views in self._err.items():
            for w,v in err_views.items():
                if win_id==w and view_id in v:
                    print task_id
                    err_free = False
                    break

        if err_free:
            print "no errs"
            view.erase_regions('shebang.mark')
            view.settings().erase('shebang.errorline')
            view.settings().erase('shebang.hop')
            return

        def blinkenlights(ttl=4):
            if ttl%2:
                view.add_regions('shebang.mark', [blinkenlights.region[-1]], 'comment', '', sublime.DRAW_OUTLINED)    
            else:
                view.add_regions('shebang.mark', [blinkenlights.region[-1]], 'comment', '', sublime.HIDDEN)    
            if ttl:
                sublime.set_timeout(functools.partial(blinkenlights,ttl-1), 90)

        blinkenlights.region = [Region(int(a),int(b)) for a,b in view.settings().get('shebang.errorline')]
        blinkenlights()

        if view.settings().has('shebang.hop'):
            errline = blinkenlights.region[-1]
            view.show_at_center(errline)
            point = Region(errline.b,errline.b)
            view.sel().add(point)
            view.settings().erase('shebang.hop')
    
            #     if parent_view and task_id.path==file_path:
            #         errline = parent_view.split_by_newlines(Region(0,parent_view.size()))[int(lineno)-1]
            #         parent_view.sel().clear()
            #         focus = Region(errline.b,errline.b)
            #         parent_view.sel().add(focus)
            #         parent_view.show_at_center(errline)
            #         parent_view.add_regions('shebang.errorline', [errline], 'invalid', '', sublime.DRAW_OUTLINED)
            #         break
