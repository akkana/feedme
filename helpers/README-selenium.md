# Configuring Selenium with Headless Firefox on Linux without a Desktop

Specifically, this is what I needed to do on our Debian server,
which did not previously have X or any related programs.


## Install Selenium

```
apt install --no-install-recommends --no-install-suggests python3-selenium
```

## Install Firefox

Download some version of Firefox from mozilla.org.
I opted for the
[extended release version](https://www.mozilla.org/en-US/firefox/all/#firefox-desktop-esr) .
I installed it in *~/firefox-esr*.

You also need geckodriver, which doesn't come with Firefox.
Despite Mozilla having its own fileservers and not using git as
its version control system, for some reason geckodriver only seems
to be available from GitHub:
[github.com/mozilla/geckodriver/releases](https://github.com/mozilla/geckodriver/releases) .
The tarballs there extract into a single executable.
I moved that executable into *~/firefox-esr*, where I'd installed Firefox,
so I'd only need to add one directory to my PATH. Selenium lets you
specify the path to the geckodriver executable, but then how will
geckodriver find Firefox if it's not in your PATH?


## Set up a virtual X environment

Our server didn't have X installed since it's never used as a desktop
machine.
Unfortunately, browsers (both Firefox and Chromium)
require libraries for X11 and a GUI toolkit (like GTK),
even when they're running in headless mode.

For Firefox,

```
apt install --no-install-recommends --no-install-suggests \
    xvfb python3-xvfbwrapper libgtk-3-0 libdbus-glib-1-2
```

This pulls in a total of 61 packages, 51 of which are from libgtk-3.0.

Now, from somewhere where you can display
X clients -- for instance, log in to your remote server with
`ssh -X servername`
and verify that Firefox runs.

```
localhost% ssh -X servername
servername% cd firefox-esr
servername% ./firefox -p
```

The -p tells firefox to start the profile manager so you can create a
new profile. (Copying the profile from another machine may not be an
option if the firefox versions are significantly different.)
I named my profile "selenium"; the nyt-selenium helper will
look for a profile of that name. After that, you can start firefox
with `./firefox -P selenium`.




FeedMe dependencies:
python3 python3-feedparser python3-lxml python3-bs4




## Copying Securely to the Public Server

Now I had it working on our home fileserver. But now I needed a way
to copy it to the actual web server, so my regular feedfetcher run
will pick it up there whether I'm at home or away.

For copying files between machines, I favor rsync over ssh.
But since this will be running automatically from cron, it needs
to run without a password -- I need to set up an ssh key.
It's easy enough to
[set up an ssh key for passwordless file
copies](https://shallowsky.com/blog/linux/ssh-keys-passwordless.html),
but you might not want to allow passwordless key access to any
important accounts, like your personal user account or the web server's user.

In that case, one option is to create a new user without a password
or a login shell:
set the login shell to something like */bin/false*, then set up the
key in the new user's *~/.ssh*. You won't be able to do the normal


## Problems with this

Apparently rsync over ssh requires shell access: it fails silently.

To try to get error messages, try (from moon)

rsync -v  -e 'ssh -vvvv' urls feeb:feeds/

But the error isn't clear, just

debug2: channel 0: chan_shutdown_write (i0 o1 sock -1 wfd 5 efd 6 [write])
rsync: connection unexpectedly closed (0 bytes received so far) [sender]
debug2: channel 0: output drain -> closed
debug3: receive packet: type 98
rsync error: error in rsync protocol data stream (code 12) at io.c(228) [sender=3.2.3]

Trying scp instead of rsync gets a

lost connection

instead of the error in protocol data stream, but still no help.

I found a few pages recommending using something called rssh as the
login shell, but that's not in either Debian or Ubuntu repositories,
and it apparently has quite a history of security holes.

However, you may be able to restrict the user to a predefined set
of commands:
https://stackoverflow.com/questions/402615/how-to-restrict-ssh-users-to-a-predefined-set-of-commands-after-login

A chrooted user might also be something to pursue.



## Older Crap

(If you don't want to install xvfbwrapper, see other approaches in
https://stackoverflow.com/questions/6183276/how-do-i-run-selenium-in-xvfb/6300672#6300672 )

In theory now you can do:

```
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from xvfbwrapper import Xvfb
import os

foxprofiledir = os.path.expanduser("~/.mozilla/firefox/someprofile/")

options = Options()
options.headless = True

executable_dir = os.path.expanduser("~/firefox-esr")
os.environ["PATH"] = "%s:%s" % (executable_dir, os.environ["PATH"])

vdisplay = Xvfb()
vdisplay.start()

sbrowser = webdriver.Firefox(firefox_profile=foxprofiledir,
                             options=options)
```

Except no, that didn't work:

```
<stdin>:1: DeprecationWarning: firefox_profile has been deprecated, please pass in a Service object
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/usr/lib/python3/dist-packages/selenium/webdriver/firefox/webdriver.py", line 170, in __init__
    RemoteWebDriver.__init__(
  File "/usr/lib/python3/dist-packages/selenium/webdriver/remote/webdriver.py", line 152, in __init__
    self.start_session(capabilities, browser_profile)
  File "/usr/lib/python3/dist-packages/selenium/webdriver/remote/webdriver.py", line 249, in start_session
    response = self.execute(Command.NEW_SESSION, parameters)
  File "/usr/lib/python3/dist-packages/selenium/webdriver/remote/webdriver.py", line 318, in execute
    self.error_handler.check_response(response)
  File "/usr/lib/python3/dist-packages/selenium/webdriver/remote/errorhandler.py", line 242, in check_response
    raise exception_class(message, screen, stacktrace)
selenium.common.exceptions.WebDriverException: Message: Process unexpectedly closed with status 255

```

It turns out that if you ssh -X into the server and try running firefox,
you get:

```
XPCOMGlueLoad error for file /home/akkana/firefox-esr/libmozgtk.so:
libgtk-3.so.0: cannot open shared object file: No such file or directory
Couldn't load XPCOM.
```

https://bugzilla.mozilla.org/show_bug.cgi?id=1372998

The issue is that headless mode isn't really headless, it still requires
all the GTK-related libraries, and there are no plans to fix that
in the forseeable future.
