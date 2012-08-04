(#!) Shebang
============

A Sublime Text 2 plugin for running scripts based on the path specified in their ‘shebang’ lines and presenting the output in a separate buffer. It has additional support for Python
scripts using [virtualenv][0] and can be used from either the command palette or as part of 
a `.sublime-build` system.

Install
-------

**Automatically**

 1. Install the [Package Control][1] plugin
 2. Within ST2, show the command palette with `⇧⌘P` then select `Package Control: Install Package`
 3. Select `Shebang` from the list to install it.

**Manually**

download the current version from the [project page][2] or clone the git repository to your `Packages` directory:

    git clone https://github.com/samizdatco/sublime-text-shebang.git Shebang

Usage
-----

The plugin adds four commands to the command palette. They can be run from either a source script or its counterpart output window.

 - **Run Script**  
   *run the current file*  
 This command will be available when the file begins with a shebang line or if its name ends in `.py`. Output will appear in a separate window with one tab for each script run so far.

 - **Run Shell Command…**  
   *run an arbitrary command line*  
 Prompts the user for a command to run then executes it. When invoked from a buffer containing a runnable script, the prompt will default to the invocation used by **Run Script**.

 - **Restart Script**  
   *stop a running script then relaunch it*  
Will appear when visiting the buffer for a script that is currently running. By default
a confirmation dialog will be presented before relaunching the script, though this can be
disabled in `Shebang.sublime-settings`.

 - **Terminate Script**  
   *Stop the current script*  
In addition to its appearance in the commands palette, this can also be invoked by typing
`ctrl-c` in either the script’s view or its corresponding output buffer. 

Configuration
-------------

General preferences are stored in a file called `Shebang.sublime-settings`. To make modifications, copy the settings file from the Shebang folder into your `Packages/User` directory.

Within the file you can redefine default behaviors:

 - `confirm_terminate` *true*  
 Whether to pop up a confirmation dialogue before terminating or restarting a running script.

 - `save_on_run` *true*  
Whether to save the current script buffer prior to running it.

 - `virtualenv` *null*  
A path (or path fragment) in which a virtualenv python environment can be found. If the value is an absolute or home-relative path, Shebang will simply use the interpreter at that path.  
​  
If the value is an unrooted name, the script’s directory and all parent directories will be traversed and a subdir matching the name will be searched for. Shebang will use the match ‘closest’ in the directory hierarchy to the script (or default to system python if none is found).


Build System Integration
------------------------

Shebang can also be used within `.sublime-build` files. It provides a build command called `execute` which is a multi-process version of the `exec` command seen in Sublime’s stock build systems.

To create a custom build system, create a `*.sublime-build` file using the syntax defined in the [documentation][3], but replacing `"target":"exec"` with `"target":"execute"`. 

In addition to the standard fields, Shebang supports some extensions: 

 - `prompt` controls whether the user can edit the command line before it is executed
 - `virtualenv` if present will override the value in the `.sublime-settings` file
 - `cmd` can usually be omitted. If it is included, the build command will not inspect the file for a shebang line and will always use the `cmd` invocation instead.

Here is an example which defines a virtualenv search pattern and allows for building with
⌘B from python or shebang output windows. The `Run` variant allows ⇧⌘B to bring up the command line editor before running. 

Save this to your `Packages/User` directory as `Virtualenv.sublime-build` and it will appear in Sublime’s `Tools > Build System` menu:

    {
      "selector": "source.python,text.shebang",
      "target":"execute",
      "virtualenv":"env",
      "variants":[
        { "name": "Run", "target":"execute", "prompt":true }
      ] 
    }

Unix Only (for now)
===================

Shebang currently works on Linux and OS X. There's nothing inherently blocking Windows support, but I'm out of my element on that OS and lack a deep understanding of the path structure (or the typical locations of interpreters). If anyone sees places where the code could be modified to be less unix-centric I'd love to hear about it.

[0]: http://www.virtualenv.org/en/latest/index.html
[1]: http://wbond.net/sublime_packages/package_control
[2]: https://github.com/samizdatco/sublime-text-shebang
[3]: http://docs.sublimetext.info/en/latest/file_processing/build_systems.html

