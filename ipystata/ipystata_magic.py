from __future__ import print_function
import sys
py3 = sys.version_info > (3,)

if not py3:
    import ConfigParser
else:
    import configparser

from IPython.core.magic import (Magics, magics_class, line_magic,
                                cell_magic, line_cell_magic, needs_local_scope)
from IPython.core.magic_arguments import (argument, magic_arguments,
    parse_argstring)
from IPython.core import display
from IPython.display import Image

from IPython import version_info
if version_info[0] < 4:
    from IPython.utils.path import get_ipython_cache_dir
else:
    from IPython.paths import get_ipython_cache_dir

import time
import pandas as pd
import sys
import subprocess
import os
import re
import win32com.client
import psutil
import atexit

class iPyStata:

    def __init__(self):
        self._config_dir = os.path.join(get_ipython_cache_dir(), 'stata', 'config')
        self._config_file = os.path.join(self._config_dir, 'configuration.ini')
        self._pid_file = os.path.join(self._config_dir, 'pid_list.txt')

        if py3:
            self.Config = configparser.ConfigParser()
        else:
            self.Config = ConfigParser.ConfigParser()

        if not os.path.exists(self._config_dir):
            os.makedirs(self._config_dir)
        if not os.path.isfile(self._config_file):
            with open(self._config_file, 'w') as configfile:
                self.Config.add_section('Stata configuration')
                self.Config.set('Stata configuration','installation',"C:\Program Files (x86)\Stata13\StataMP-64.exe")
                self.Config.write(configfile)

        self.Config.read(self._config_file)

        ## Perform a clean-up at the start (Note: this closes all previously started win32com Stata instances!)

        if os.path.isfile(self._pid_file):
            with open(self._pid_file, 'r') as pid_text:
                still_open = self.get_stata_pid()
                pids = pid_text.read().split(',')
                count_close = 0
                for x in pids:
                    if int(x) in still_open:
                        psutil.Process(int(x)).terminate()
                        count_close += 1
                if count_close > 0:
                    print('Terminated %d unattached Stata session(s).' % count_close)

        time.sleep(1)

        stata_dir = os.path.join(get_ipython_cache_dir(), 'stata')
        for x in os.listdir(stata_dir):
            if re.match('^log_.*', x):
                try:
                    os.remove(os.path.join(stata_dir, x))
                except:
                    pass

    def ConfigSectionMap(self, section):
        dict1 = {}
        options = self.Config.options(section)
        for option in options:
            try:
                dict1[option] = self.Config.get(section, option)
                if dict1[option] == -1:
                    DebugPrint("skip: %s" % option)
            except:
                print("exception on %s!" % option)
                dict1[option] = None
        return dict1

    def process_log(self, log):
        with open(log, 'r+') as log_file:
            code = log_file.read()
            code = re.sub('^-*\\n', "", code)
            code = re.sub('-*\\n$', "", code)
            code = re.sub('\s*name: *.*?\\n', "\n", code)
            code = re.sub('\s*log: *.*?\\n', "\n", code)
            code = re.sub('\s*log type: *.*?\\n', "\n", code)
            code = re.sub('\s*opened on: *.*?\\n', "\n", code)
            code = re.sub('\s*closed on: *.*?\\n', "\n", code)
            code = re.sub('\s*closed on: *.*?\\n', "\n", code)
            code = re.sub('\\n\\n. \n. ', '\\n\\n. ', code)
            code = re.sub('\n\.\s\n', '\n', code)
            code = re.sub("(?:\\n|^)\. ..*?\\n", "", code)
            code = re.sub("\s{1,3}[0-9]{1,2}\.\s.*?\\n", "", code)
            code = re.sub(r"\.\s\w.*?(?:\\n|$)", "", code)
            code = re.sub('\\n{1,}$', "", code)
            code = re.sub('^\\n{0,}', "\n", code)
            log_file.truncate(0)
        return code

    def get_stata_pid(self):
        pid_list = []
        for proc in psutil.process_iter():
            try:
                if re.match('stata', proc.name(), flags=re.I):
                    pid_list.append(proc.pid)
            except:
                pass
        return pid_list

iPyStata = iPyStata()

def clean_at_close():
    if os.path.isfile(iPyStata._pid_file):
        with open(iPyStata._pid_file, 'r') as pid_text:
            still_open = iPyStata.get_stata_pid()
            pids = pid_text.read().split(',')
            for x in pids:
                if int(x) in still_open:
                    psutil.Process(int(x)).terminate()

atexit.register(clean_at_close)

@magics_class
class iPyStataMagic(Magics):

    def __init__(self, shell):
        super(iPyStataMagic, self).__init__(shell)
        self._lib_dir = os.path.join(get_ipython_cache_dir(), 'stata')
        self._pid_file = iPyStata._pid_file
        if not os.path.exists(self._lib_dir):
            os.makedirs(self._lib_dir)

        self.session_dict = {}
        self.do_dict = {}
        self.log_dict = {}
        self.pid_list = []

    @magic_arguments()

    @argument('-i', '--input', action='append', help='This is an input argument.')
    @argument('-d', '--data', help='This is the data input argument.')
    @argument('-o', '--output', help='This is the output argument.')
    @argument('-np', '--noprint', action='store_true', default=False, help='Force the magic to not return an output.')
    @argument('-os', '--openstata', action='store_true', default=False, help='Open Stata.')
    @argument('-s', '--session', default='main', help='The name of a Stata session in which the cell has to be executed.')
    @argument('-cwd', '--changewd', action='store_true', default=False, help='Set the current Python working directory in Stata session.')
    @argument('-gm', '--getmacro', action='append', help='This will attempt to output the named macro values as a dictionary.')
    @argument('-gr', '--graph', action='store_true', default=False, help='This will classify the Stata cell as one that returns a graph.')

    @needs_local_scope
    @cell_magic
    def stata(self, line, cell, local_ns=None):

        def active_check(session):
            try:
                self.session_dict[session].UtilIsStataFree()
                return True
            except:
                return False

        def stata_list(l):
            return ' '.join([str(x) for x in l])

        ## Debug mode (Delete before pushing)

        if re.match(r'^debug$', cell, flags=re.I):
            args = parse_argstring(self.stata, line)
            return args.getmacro

        ## Support functions: sessions, close, close all, reveal all, hide all

        if re.match(r'^sessions$', cell, flags=re.I):
            print('The following sessions have been found:')
            for z in self.session_dict.keys():
                if active_check(z):
                    print(z + ' [active]')
                else:
                    print(z + ' [not active]')
            return

        if re.match(r'^close$', cell, flags=re.I):
            print('The following sessions have been closed:')
            close_list = []
            for z in self.session_dict.keys():
                if active_check(z):
                    self.do_dict[z]('exit')
                    close_list.append(z)
                    print(z)
            for z in close_list:
                self.session_dict.pop(z, None)
            time.sleep(1)
            if os.path.isfile(self._pid_file):
                with open(self._pid_file, 'r') as pid_text:
                    still_open = iPyStata.get_stata_pid()
                    pids = pid_text.read().split(',')
                    for x in pids:
                        if int(x) in still_open:
                            psutil.Process(int(x)).terminate()
                            print('Terminated unattached Stata session.')
            self.pid_list = []
            return

        if re.match(r'^close all$', cell, flags=re.I):
            closed_count = 0
            for x in iPyStata.get_stata_pid():
                psutil.Process(x).terminate()
                closed_count += 1
            self.session_dict = {}
            print('Terminated %d running Stata processes' % closed_count)
            return

        if re.match(r'^reveal all$', cell, flags=re.I):
            reveal_count = 0
            for x in self.session_dict.keys():
                if active_check(x):
                    self.session_dict[x].UtilShowStata(0)
                    reveal_count += 1
            print('Revealed %d Stata sessions.' % reveal_count)
            return

        if re.match(r'^hide all$', cell, flags=re.I):
            hide_count = 0
            for x in self.session_dict.keys():
                if active_check(x):
                    self.session_dict[x].UtilShowStata(1)
                    hide_count += 1
            print('%d Stata sessions have been hidden.' % hide_count)
            return

        ## Obtain the current working directory and if requested set as Stata working directory.

        python_cwd = os.getcwdu() if sys.version_info[0] == 2 else os.getcwd()
        #os.chdir(self._lib_dir)

        args = parse_argstring(self.stata, line)

        if local_ns is None:
            local_ns = {}

        stata_lists = []
        name_list = []

        if args.input:
            for input in ','.join(args.input).split(','):
                try:
                    val = local_ns[input]
                except KeyError:
                    try:
                        val = self.shell.user_ns[input]
                    except KeyError:
                        raise NameError("name '%s' is not defined" % input)
                if isinstance(val, list):
                    stata_lists.append(stata_list(val))
                    name_list.append(input)
                else:
                    pass

        data_dir = os.path.join(self._lib_dir, 'data_input.dta')
        if args.data:
            data = args.data
            try:
                val = local_ns[data]
            except KeyError:
                try:
                    val = self.shell.user_ns[data]
                except KeyError:
                    raise NameError("name '%s' is not defined" % data)
            if isinstance(val, pd.DataFrame):
                val = val.reset_index()
                date_var = []
                for x in val.columns.values:
                    if val[x].dtype == "datetime64[ns]":
                        date_var.append(x)
                    else:
                        pass
                if date_var:
                    val.to_stata(data_dir, convert_dates= {x:'tc' for x in date_var}, write_index=False)
                else:
                    val.to_stata(data_dir, write_index=False)
            else:
                pass

        ## Check whether the Stata session exists and is active, status is stored.

        session_id = args.session

        if session_id in self.session_dict and isinstance(self.session_dict[session_id], win32com.client.CDispatch):
            try:
                self.session_dict[session_id].UtilIsStataFree()
                active = True
            except:
                print('Please note: session %s was no longer active and is restarted.' % session_id)
                active = False
        else:
            active = False

        ## If Stata session does not exist or became inactive; start one and store the PID.

        if not active:
            pid_before = iPyStata.get_stata_pid()
            time.sleep(0.5)
            self.log_dict[session_id] = os.path.join(self._lib_dir, 'log_%s.txt' % session_id)
            self.session_dict[session_id] = win32com.client.Dispatch("stata.StataOLEApp")
            self.do_dict[session_id] = self.session_dict[session_id].DoCommandAsync
            self.session_dict[session_id].UtilShowStata(1)
            self.do_dict[session_id]('log using {} , text replace'.format(self.log_dict[session_id]))
            self.do_dict[session_id]('set more off')

            pid_after = iPyStata.get_stata_pid()
            if len(pid_before) == 1 and pid_before == pid_after:
                self.pid_list.append(pid_after[0])
            else:
                try:
                    self.pid_list.append(list(set(pid_after) ^ set(pid_before))[0])
                except:
                    print('PID could not be obtained, can only be closed by using "close all".')

        ## Send Stata session to foreground / background.

        if args.openstata:
            self.session_dict[session_id].UtilShowStata(0)
        else:
            self.session_dict[session_id].UtilShowStata(1)

        ## Build the command that is send to State.
            ## Note, combination is to simplify log handling (only one log output has to be processed).

        data_out = os.path.join(self._lib_dir, 'data_output.dta')
        graph_out = os.path.join(self._lib_dir, 'temp_graph.png')
        code_list = []
        for x,y in zip(name_list, stata_lists):
            code_list.append("local "+ x + ' ' + y + "\n")
        if args.data:
            code_list.append(r'use "%s"' % data_dir + "\n")
        if args.changewd:
            code_list.append('quietly cd "%s"'% python_cwd + '\n')
            print('Set the working directory of Stata to: %s' % python_cwd)
        code_list.append(cell)
        if args.output:
            code_list.append('quietly save "%s", replace ' % data_out + "\n")
        if args.graph:
            code_list.append('quietly graph export "%s", replace width(1000) height(800) ' % graph_out + "\n")
        code_txt= '\n'.join(code_list)

        ## Execute code and wait for the Stata session to finish.

        self.do_dict[session_id](code_txt)

        while self.session_dict[session_id].UtilIsStataFree() == 0:
            pass

        ## Give the log file a second to save and then process it.

        time.sleep(1)
        out = iPyStata.process_log(self.log_dict[session_id])

        if args.output:
            try:
                output_ipys = pd.read_stata(data_out)
                try:
                    output_ipys.drop('index', axis=1, inplace=True)
                    self.shell.push({args.output: output_ipys })
                except:
                    self.shell.push({args.output: output_ipys })
            except:
                print("Exception has occured. File could not be loaded. (Note, Pandas needs to be 0.17.x or higher.)")

        if args.getmacro:
            macro_dict = {}
            count_success = 0
            fail_list = []
            for x in args.getmacro:
                try:
                    temp_macro = self.session_dict[session_id].MacroValue("_%s" % x).split(' ')
                    if temp_macro == ['']:
                        fail_list.append(x)
                    else:
                        macro_dict[x] = temp_macro
                        count_success += 1
                except Exception as e:
                    print(e)
                    pass
            if len(fail_list) > 0:
                print("The following macros could not be found: %s" % ', '.join(fail_list))
            if len(macro_dict.keys()) > 0:
                self.shell.push({'macro_dict' : macro_dict})
                print("Several (%dx) macros have been added to the dictionary: macro_dict" % count_success),

        with open(self._pid_file, 'w') as pid_text:
            pid_text.write(','.join(map(str, self.pid_list)))

        if not args.noprint:
            if args.graph:
                if not len(out) < 5:
                    print(out)
                if re.search('could not find Graph window', out, flags=re.I) is None:
                    return Image(graph_out, retina=True)
                else:
                    print('\nNo graph displayed, could not find one generated in this cell.')
            else:
                return print(out)
        else:
            return

# Register the magic function:
ip = get_ipython()
ip.register_magics(iPyStataMagic)

# Enable the stata syntax highlighting:
js = "IPython.CodeCell.config_defaults.highlight_modes['magic_stata'] = {'reg':[/^%%stata/]};"
display.display_javascript(js, raw=True)
