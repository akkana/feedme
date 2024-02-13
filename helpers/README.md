
# FeedMe Helpers

Some sites make fetching feeds too complicated to handle through
the normal FeedMe process.

For instance, a site may not have RSS, so you need to write code to
parse an HTML page. Or you may need to fetch pages on a machine other
than the one that's normally assembling your feeds.

Enter feedme helpers. There are two types of helpers: ```page_helpers```
and ```feed_helpers```.

Helpers will be imported as Python modules, so they must either
be in the *helpers/* directory wherever feedme is installed, or
somewhere in your PYTHONPATH. They also must conform to Python
module naming rules, e.g. nyt_selenium.py, not nyt-selenium.py.


## Passing Arguments to Helpers

Both types of helpers can take arguments. Any argument beginning with
`helper_` in the site file will be passed to the helper.
For instance, *nyt_selenium* takes two arguments:

```
helper_executable_path = ~/firefox-esr
helper_log_file = $d/NYT_selenium.log
```

A $d in the arg string will be expanded to the current day's
feeds directory, e.g. `~/feeds/10-07-Wed`,
before being passed to the helper.
$f will be expanded to the feed name inside $d, e.g.
`~/feeds/10-07-Wed/NY_Times/`.
If you need a literal $d or $f, use a backslash escape, `\$d`.

For page helpers, you can also use the *url* in the site file to
pass information. url must be set to something anyway, even if it's
not used: feedme uses the presence of url to determine if a site file
is valid.

When specifying directories, you'll probably need to specify an
absolute path (in place of ~/) if you call feedme from a web server,
since it isn't running as your user. Tilde expansion is done (or not)
inside the helper, since helper_args may not always be filenames.
Any expansion such as tilde (expanduser) expansion is left to
the helper modules, since feedme doesn't know how the helper_arg
will be used by the helper. $d and $f expansion are done in feedme
before passing them to the helper.


## Page Helpers:

A ```page_helper``` can fetch single pages
one at a time. The HTML will be passed back to feedme, which can
then do its normal operations and store the result in the feeds directory.
This assumes that the site has a usable RSS feed, and help is only
needed for fetching the individual articles.

Let's say you want to feed the New York Times. You're a subscriber,
and you have a Firefox profile with the appropriate cookies.

In your site file, add a line like

```
page_helper = nyt_selenium
```

That tells feedme to import the helper module nyt_selenium.py.

nyt_selenium.py must define the following functions:

```
initialize(helper_arg)
    # can be a no-op if no persistent state is needed

fetch_article(url)
    # Returns the desired html as a string (not bytes).
    # This can be further processed by feedme
    # if your site file has directives like page_start,
    # skip_pat etc.
```


## Feed Helpers

A ```feed_helper``` fetches a whole feed at once.
For instance, a site that has no RSS would need a feed helper,
since feedme can only get the list of stories from RSS.
With a feed helper you would have to manage last-seen dates
on your own.

For instance, to use the copyfeed helper, add a line like

```
feed_helper = copyfeed
helper_arg = ~/feeds/$d/New_York_Times/
```

But see the comment above under *Passing Arguments to Helpers*
regarding ~.

To write a feed helper, you must implement one call:

```
fetch_feed(target_dir, helper_arg)
```

The ```target_dir``` is the target feed directory,
e.g. ```.../feeds/10-06-Wed/``` into which the final files should be written.
The helper is responsible for creating that directory,
if it's successful in getting feed files.

All files in the target directory will be added to the day's ```MANIFEST```.
