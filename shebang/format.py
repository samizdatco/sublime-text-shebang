# encoding: utf-8
import os, sys, re
import datetime
import sublime
import functools
from collections import defaultdict
from os.path import join, exists, normpath, relpath, basename, dirname
from itertools import izip_longest as izipl
from sublime import Region

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

        status = inv['cmd'] if inv.get('shell') else basename(inv['task'].path)
        view.set_name(u'… %s'%status)

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
        view.set_read_only(False)

        exit_code = info['exit_code']
        elapsed = info['elapsed']
        cwd = info['working_dir']

        begin = view.find_by_selector('comment.header.shebang')[-1]
        run_body = view.substr(Region(begin.b+3, view.size()))

        errstr = ''
        if 0 < exit_code < 11:
            errstr = u"⓵⓶⓷⓸⓹⓺⓻⓼⓽⓾"[exit_code-1]
        elif exit_code:
            errstr = str(exit_code)


        sizestr = self._pretty('size',view.size()-begin.b-3)
        timestr = self._pretty('time', elapsed)
        

        self.append_txt(view, u'\n%s%s %s %s%s\n'%(self.m_result,timestr,sizestr,errstr, self.m_end))
        edit = view.begin_edit()
        # view.insert(edit, view.size(), u'\n%s%s %s %s%s\n'%(self.m_result,timestr,sizestr,errstr, self.m_end))
        for r in view.find_by_selector('keyword.pid.shebang'):
            view.erase(edit, Region(r.a-2, r.b+1))
        view.end_edit(edit)
        view.set_read_only(True)
        view.erase_status("shebang:running")



        status = info['arg_list'] if info.get('shell') else basename(info['task'].path)
        if exit_code:
            view.set_name(u'%s %s'%(errstr, status))

            try:
                parent_win = [w for w in sublime.windows() if w.id()==task_id.window][0]
            except IndexError:
                return

            views = dict([(v.file_name(), v) for v in parent_win.views()])


            re_file = re.compile(info['file_regex'], re.M)
            stack_frames = []
            for m in re_file.finditer(run_body):
                fn = m.group(1)
                file_path = join(cwd, fn)
                if not exists(file_path) and exists(fn):
                    file_path = fn
                stack_frames.append(dict(start=m.start(), end=m.end(), path=file_path, line=int(m.group(2))))

            for first, next in izipl(stack_frames, stack_frames[1:]):
                if next:
                    rng = Region(first['end'], next['start'])
                else:
                    rng = Region(first['end'], len(run_body))
                # print first['path']
                # print "]%s["%run_body[rng.a:rng.b]

                m = re.search(r'in ([^\n]*)\n([^\n]+)\n', run_body[rng.a:rng.b], re.S)
                if m:
                    # first['line'] = "%s%s"%(m.group(1).strip(), 
                                            # "" if m.group(1).endswith('>') else "()")
                    # first['context'] = m.group(2).strip()
                    first['context'] = dict(fn="%s%s"%(m.group(1).strip(), 
                                               "" if m.group(1).endswith('>') else "()"),
                                            src=m.group(2).strip() )
                    
                    # first['context'] = "%s%s: %s"%(m.group(1).strip(), 
                    #     "" if m.group(1).endswith('>') else "()",
                    #     m.group(2).strip())

            panel = parent_win.get_output_panel("shebang")
            panel.settings().set("result_file_regex", info['file_regex'])
            panel.settings().set("result_line_regex", info['line_regex'])
            panel.settings().set("result_base_dir", info['working_dir'])
            panel = parent_win.get_output_panel("shebang")

            if stack_frames:
                run_body = run_body[stack_frames[0]['start']:]

            panel.set_read_only(False)
            edit = panel.begin_edit()
            panel.insert(edit, panel.size(), run_body)
            panel.show(panel.size())
            panel.end_edit(edit)
            panel.set_read_only(True)
            parent_win.run_command("show_panel", {"panel": "output.shebang"})

            err_idx = defaultdict(set) # {w_id:[v1,v2,v3], ...}
            err_rgns = defaultdict(list) # {v_obj: [[a,b], [c,d], ...]}
            for frame in reversed(stack_frames):
                parent_view = views.get(frame['path'])
                if parent_view:
                    err_idx[parent_win.id()].update([parent_view.id()])
                    errline = parent_view.split_by_newlines(Region(0,parent_view.size()))[frame['line']-1]
                    err_rgns[parent_view].append([errline.a, errline.b])

            for err_view, errs in err_rgns.items():
                err_view.settings().set('shebang.err_ln', errs)
                err_view.settings().set('shebang.hop',"hop")
            if err_idx:
                self._err[task_id] = dict(views=dict((k,list(v)) for k,v in err_idx.items()), 
                                          trace=stack_frames)
            elif task_id in self._err:
                del self._err[task_id]

            for win in sublime.windows():
                if win.active_view().id() in err_idx[win.id()]:
                    self.flash_errors(win.active_view())
        else:
            # yay, no errors
            view.set_name(u'✓ %s'%(status))

            # view.set_name(u'%s (%s, %s)'%(status,timestr,sizestr))
            self.clear_errors(task_id)
        
    def clear_errors(self, task_id):
        if task_id in self._err:
            del self._err[task_id] 

    def flash_errors(self, view):
        err_free = True
        win_id, view_id = view.window().id(), view.id()
        for task_id, err in self._err.items():
            for w,v in err['views'].items():
                if win_id==w and view_id in v:
                    err_free = False
                    break

        if err_free:
            view.erase_regions('shebang.mark')
            view.settings().erase('shebang.err_ln')
            view.settings().erase('shebang.hop')
            return

        def blinkenlights(ttl=4):
            if ttl%2:
                view.add_regions('shebang.mark', [blinkenlights.region[0]], 'comment', '', sublime.DRAW_OUTLINED)    
            else:
                view.add_regions('shebang.mark', [blinkenlights.region[0]], 'comment', '', sublime.HIDDEN)    
            if ttl:
                sublime.set_timeout(functools.partial(blinkenlights,ttl-1), 90)

        blinkenlights.region = [Region(int(a),int(b)) for a,b in view.settings().get('shebang.err_ln')]
        sublime.set_timeout(functools.partial(blinkenlights), 90)

        if view.settings().has('shebang.hop'):
            errline = blinkenlights.region[0]
            view.show_at_center(errline)
            # point = Region(errline.b,errline.b)
            # view.sel().clear()
            # view.sel().add(point)
            view.settings().erase('shebang.hop')

    def browse_stacktrace(self, task_id, inv):
        err = self._err.get(task_id)
        if err:
            file_paths = []
            ui = []

            for frame in err['trace']:
                pth = frame['path']
                file_paths.append('%s:%i'%(pth, frame['line']))
                rel_pth = relpath(pth, inv['working_dir'])
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
                if idx>0:
                    for win in (w for w in sublime.windows() if w.id()==task_id.window):
                        return win.open_file(file_paths[idx],sublime.ENCODED_POSITION)
                    sublime.active_window().open_file(file_paths[idx],sublime.ENCODED_POSITION)


            sublime.active_window().show_quick_panel(ui, outcome)
            #don't forget to mark the error lines in the newly opened view...












