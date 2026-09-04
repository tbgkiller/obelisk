# Getting back into the admin UI

_Design note. The recovery path below is not built yet; the diagnosis and the interim
workaround are real and were arrived at by locking a real operator out._

## Two passwords, and only one of them opens this door

Obelisk has two secrets that people reasonably confuse, because both are called "the
admin password" by somebody:

| | What it is | What it opens |
|---|---|---|
| **Setup code** (`admin_token`) | Generated once, on first boot | The Obelisk **web UI** |
| **Admin password** (`admin_password`) | Chosen by the operator | **In-game admin and RCON**, on every map |

They are unrelated. The game password is the one that matters day to day - it is what
the cluster runs with and what every RCON tool uses - so it is the one an operator
remembers, and it is the one they will try at the setup prompt. It will not work, and
the page will not explain why, because from its point of view a wrong code is a wrong
code.

That is the whole of the confusion, and it is worth stating in the UI rather than here.

## Why it asks again

The session cookie is the token itself, set with no lifetime:

```python
resp.set_cookie(COOKIE, token, httponly=True, samesite="Lax")
```

No `max_age` and no `expires` makes it a **session cookie**: the browser throws it away
when it closes. So the prompt comes back after a browser restart, in a private window,
or on a second device - none of which is a failure, and none of which is obvious.

Worth being precise about what does *not* cause it. Restarting, updating or recreating
the container does not: the token lives in `settings.json` on the mounted data folder,
survives the container entirely, and is compared by value. A cookie issued before a
recreate still works afterwards.

## Why the code cannot be found again

`firstrun.bootstrap()` prints the banner only when it has just generated a code:

```python
if not str(store.get("admin_token")).strip():
    setup_code = secrets.token_urlsafe(9)
    store.patch({"admin_token": setup_code})
...
if setup_code:
    log.warning(... "Setup code:  %s" ...)
```

On every later boot `admin_token` is already set, so `setup_code` is `None` and nothing
prints. The code exists, it is valid forever, and there is no way to see it - not in the
log, not in the UI, which is behind the code. The one place it can be read is the file,
which means a shell.

So the failure is complete: the code is unobtainable through any interface Obelisk
offers, and the password the operator knows opens a different lock.

## Right now, from a shell

```sh
docker exec <container> python3 -c \
  "import json; print(json.load(open('/data/settings.json'))['cluster']['admin_token'])"
```

It is stored in plain text, so what comes out is the code to type.

## The fix

Three parts, smallest first. The first two are worth doing together; the third is a
judgement call for the owner.

**1. Print it on every boot when the UI is still locked.** If no browser has ever
successfully authenticated, the operator is by definition still in setup, and hiding the
code from them protects nobody. Keep the current banner for a genuinely new install, and
add a quieter one-liner on later boots that names the code and says where to enter it.
Once setup is complete, stop printing it.

**2. A command that shows or rotates it.** Recovery should not require knowing the shape
of `settings.json`:

```sh
docker exec <container> obelisk code          # show the current one
docker exec <container> obelisk code --new    # rotate, and log out every session
```

Anyone who can run `docker exec` can already read the file, so showing it grants nothing
new - it just stops the operator having to reverse-engineer the storage. Rotation is the
part that earns its keep: it is what you want after sharing the code, or when you are not
sure who has it.

**3. Say the two passwords apart, on the page that asks.** One line under the setup
field: *this is the setup code from the container log, not your in-game admin password*.
That is the actual bug report, and it costs a sentence.

**Optionally: let the admin password unlock the UI as well.** Tempting, and it removes
the confusion by making the wrong guess correct. But it widens what that password
controls: it is shared with every map, it is handed to RCON tools, it appears in the
server's own command line - `ps` inside any map container shows it - and it is in
archives. Making it also the key to the settings UI means every one of those exposures
becomes a way in. Keeping the UI on a secret that exists only in the store, and never on
a command line, is the better trade. The confusion is worth fixing with a sentence rather
than by widening the blast radius.

## Also worth fixing while here

The cookie should carry an explicit lifetime rather than defaulting to "until the browser
closes", so that "stay logged in" is a decision somebody made rather than a default
nobody chose.
