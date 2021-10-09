# FeedMe Helpers

Some sites make fetching feeds too complicated to handle through
the normal FeedMe process.

For instance, the New York Times doesn't allow fetching stories
through their API, and even if you're a subscriber, they don't allow
any simple method for providing login credentials: you have to have a
full browser environment with JavaScript and a logged-in browser
profile.

To make things more complicated, you may need to fetch pages on a
machine other than the one that's normally assembling your feeds.
For instance, you may not want to run a desktop browser (and all
the X machinery it needs) on your web server.

Enter feedme helpers. There are two types of helpers: ```page_helpers```
and ```feed_helpers```.


## Passing Arguments to Helpers

Both types of helpers can take one string argument, set in the site
file: ```helper_arg```

This will be passed to the helper's ```initialize(helper_arg)``` function.
If the helper needs multiple arguments, encode it in the helper_arg string.

By convention, a $d in the arg string will be expanded to the current
day's string as used in the feeds directory, e.g. 10-07-Wed.

For page helpers, you can also use the *url* in the site file to
pass information. url must be set to something anyway, even if it's
not used: feedme uses the presence of url to determine if a site file
is valid.


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

This tells feedme to import a file nyt_selenium.py, which must either
be in the *helpers/* directory wherever feedme is installed, or
somewhere in your PYTHONPATH.

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

You'll probably need to specify an absolute path (in place of ~/)
if you call feedme from a web server, since it isn't running as your user.
Tilde expansion is done (or not) inside the helper, since helper_args
may not always be filenames.

To write a feed helper, you must implement one call:

```
fetch_feed(target_dir, helper_arg)
```

The ```target_dir``` is the target feed directory,
e.g. ```.../feeds/10-06-Wed/``` into which the final files should be written.
The helper is responsible for creating that directory,
if it's successful in getting feed files.

All files in the target directory will be added to the day's ```MANIFEST```.
