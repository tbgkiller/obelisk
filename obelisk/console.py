"""One map's RCON console: what may be typed at it, and what the answer means.

Two jobs, both of them judgement rather than plumbing. The plumbing - opening a socket,
authenticating, sending the line - is bot.rcon_with and has been for as long as the
relay has existed.

**Which commands need asking about first.** A console that will send anything is a
console that will send `DoExit` to a full map because the operator was looking at a
different tab. So the destructive and the disruptive ones are named here and gated by a
confirmation. Classified on the *first token only*, lowercased: the effect of a line is
decided by its verb and by nothing else, and a substring test would refuse
`ServerChat DestroyAll is banned` - an announcement - while a `destroyall` typed in
lowercase sailed past. The word in the argument is a word. The word in front is an act.

**What the answer is worth.** This is the half that matters and the half that is easy to
get wrong, because ARK answers most commands that change something with

    Server received, But no response!!

which is the server saying "I took this line" and nothing whatsoever about what it did
with it. Rendering that as success is how an operator comes to believe a save landed
when the only thing that landed was the request. So it has its own verdict, in its own
words, and the words are *delivered, not confirmed*. An empty answer, a refused
connection and a command that ran out of time are three more different things, and each
one is its own verdict too - a console that flattens them into "failed" is a console
that cannot tell "the server is down" from "the server is busy saving".

Nothing here opens a socket, reads the disk or touches the store, so all of it is
testable without a cluster.
"""

# ---------------------------------------------------------------- what to offer
#
# Deliberately short. These are the commands that answer a question and change nothing,
# plus the one write the operator needs beside them: a hand-driven stop is
# ListPlayers -> SaveWorld -> look at the disk -> DoExit, and the last step is typed
# rather than offered, because a button for it is a button somebody clicks.
CURATED = (
    ("ListPlayers", "who this map says is on it right now"),
    ("SaveWorld", "ask this map to write its world to disk now"),
    ("ListPlayerPos", "where each of them is standing"),
)

# GetChat and GetGameLog are *not* buttons, and that is not an oversight. Both are
# consuming reads: ARK hands over the lines accumulated since the last call and then
# forgets them. The chat relay polls both every couple of seconds, so reading them from
# here takes chat and tribe-log lines out of the feed on their way to Discord - a button
# that looks like a look and is quietly a deletion. They can still be typed, which is
# the difference between a thing you chose to do and a thing a button did for you.
CONSUMING = ("getchat", "getgamelog")
CONSUMING_WHY = ("GetChat and GetGameLog hand over the lines since the last read and "
                 "then forget them, so anything read here is taken out of the chat "
                 "relay’s feed on its way to Discord. They are not offered as "
                 "buttons for that reason — typing one is fine, as long as that "
                 "is what you meant.")


def consuming(command):
    """Does this command take something away from the relay by reading it?"""
    return first_token(command) in CONSUMING


# ---------------------------------------------------------------- what to ask about
#
# Named by first token, lowercased. Every Destroy* command destroys, including the ones
# this list has never heard of - DestroyTribeIdDinos, and whatever the next build adds -
# so that family is matched by its verb rather than enumerated. Over-gating costs a
# click. Under-gating costs a world.
GATED = frozenset((
    "shutdown", "doexit",
    "kick", "kickplayer",
    "ban", "banplayer",
    "killplayer", "clearplayerinventory",
))
GATED_PREFIXES = ("destroy",)

# What sending it will actually do, said in the confirmation. Keyed by the same first
# token. A question that only names the command is a question nobody can answer without
# already knowing the answer.
EFFECTS = {
    "doexit": ("stop %s. The server exits, everyone on it is disconnected, and this "
               "map stays down until something starts it again"),
    "shutdown": ("stop %s. The server exits, everyone on it is disconnected, and this "
                 "map stays down until something starts it again"),
    "kick": "disconnect somebody from %s. They can rejoin straight away",
    "kickplayer": "disconnect somebody from %s. They can rejoin straight away",
    "ban": ("ban somebody from %s. ARK keeps a ban list per server, so this is %s and "
            "no other map"),
    "banplayer": ("ban somebody from %s. ARK keeps a ban list per server, so this is "
                  "%s and no other map"),
    "killplayer": "kill somebody on %s. They lose whatever they were carrying",
    "clearplayerinventory": ("empty somebody’s inventory on %s. What it takes is "
                             "gone - there is no undo in the game for this"),
}
DESTROYS = ("destroy things on %s that the game cannot bring back. There is no undo "
            "for this short of a restore from a save")


def first_token(command):
    """The command word: the first whitespace-separated token, lowercased.

    The whole classification hangs off this one line, so it is written down once. A
    command with nothing in front of it - blank, or whitespace - answers "", which is
    not any command and so is gated by nothing and sent by nobody.
    """
    parts = str(command or "").split()
    return parts[0].lower() if parts else ""


def gated(command):
    """Does this command have to be confirmed before it is sent?

    First token only. `ServerChat DestroyAll is banned` is an announcement about a rule
    and is sent as typed; `destroyall` is the rule being broken and is asked about.
    """
    word = first_token(command)
    if not word:
        return False
    return word in GATED or word.startswith(GATED_PREFIXES)


def effect(command, map_name):
    """What sending this to that map will do, in one sentence. "" when it is not gated.

    The fallback matters as much as the entries: a Destroy* verb this file has never
    seen still gets a sentence saying it destroys, rather than an empty confirmation
    that names a command and describes nothing.
    """
    if not gated(command):
        return ""
    word = first_token(command)
    if word in EFFECTS:
        text = EFFECTS[word]
        return text % ((map_name, map_name) if text.count("%s") == 2 else map_name)
    return DESTROYS % map_name


# ---------------------------------------------------------------- what came back
#
# ARK's answer to most commands that change something. bot.IGNORE has held this exact
# string since the relay was written, to keep it out of the chat feed; test_app pins
# the two spellings together rather than letting this module import the relay, which
# configures logging and reads a dozen environment variables the moment it is loaded.
NO_RESPONSE = "Server received, But no response!!"

DELIVERED = "delivered"      # it arrived. Nothing is known about what it did.
ANSWERED = "answered"        # the server said something, and that something is shown
EMPTY = "empty"              # the server said nothing at all - not the same thing
TIMEOUT = "timeout"          # it ran out of time. It may still be running.
REFUSED = "refused"          # nothing was sent: the port would not open
DENIED = "denied"            # the port opened and RCON would not take the password
BROKE = "broke"              # anything else, reported as itself

# The line at the head of the box. Kept here rather than in the renderer because these
# are the claims this feature is allowed to make, and they are worth reading in one
# place next to each other.
HEADLINES = {
    DELIVERED: "Delivered, not confirmed.",
    ANSWERED: "The server answered.",
    EMPTY: "The server answered with nothing at all.",
    TIMEOUT: "No answer in time.",
    REFUSED: "Nothing was sent — the connection was refused.",
    DENIED: "Nothing was sent — RCON refused the admin password.",
    BROKE: "Nothing was sent — the connection broke.",
}

# Grey for a server that talked, amber for an outcome nobody can read as a result, red
# for a command that did not get there. The same three colours the rest of the manager
# uses, and the reason DELIVERED is amber rather than grey: it is an unknown, and an
# unknown shown in the colour of a finished thing is the failure this console exists to
# avoid.
LEVELS = {
    DELIVERED: "warn", ANSWERED: "note", EMPTY: "warn",
    TIMEOUT: "problem", REFUSED: "problem", DENIED: "problem", BROKE: "problem",
}


def verdict(body):
    """What a returned RCON body is worth. {kind, detail, text}.

    `text` is what the server said, shown as it came. `detail` is what it means, which
    for the "received" answer is the whole point: the command arrived, and that is the
    only thing anybody can say about it from here.
    """
    text = str(body if body is not None else "")
    if text.strip() == NO_RESPONSE:
        return {
            "kind": DELIVERED,
            "text": text.strip(),
            "detail": ("This map took the command and had nothing to say about it, "
                       "which is ARK’s usual answer to a command that changes "
                       "something. It proves the line arrived. It says nothing at all "
                       "about whether what it asked for happened - if that matters, "
                       "check for it some other way."),
        }
    if not text.strip():
        return {
            "kind": EMPTY,
            "text": "",
            "detail": ("The connection opened, the command went out and the answer "
                       "came back with no characters in it. That is not ARK’s "
                       "“received” reply - most often it means this server "
                       "does not know the command. Nothing here says it ran."),
        }
    return {
        "kind": ANSWERED,
        "text": text.rstrip(),
        "detail": ("What the server said, as it said it. Whether it is the whole "
                   "answer is the server’s business - RCON hands back one packet "
                   "and long output can be cut off in it."),
    }


def verdict_for_error(error, timeout=None):
    """What a failed RCON call is worth. Three different things, never one.

    Asked of the exception's type rather than of its text: "timed out" and "refused"
    and "the password was not accepted" are three different facts about the cluster,
    and an operator told only that "it failed" cannot act on any of them.
    """
    import asyncio, socket

    seconds = ("" if not timeout else
               " within %ss" % (int(timeout) if float(timeout) == int(timeout)
                                else timeout))
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, socket.timeout)):
        return {
            "kind": TIMEOUT,
            "text": "",
            "detail": ("This map did not answer%s. The command may have arrived and "
                       "still be running - a big world saving takes longer than this "
                       "wait - or the server may be wedged. Nothing here tells the two "
                       "apart, so nothing here says it did or did not run." % seconds),
        }
    if isinstance(error, ConnectionRefusedError):
        return {
            "kind": REFUSED,
            "text": "",
            "detail": ("Nothing is listening on this map’s RCON port, so the "
                       "command never left. The server is down, or still booting and "
                       "has not opened RCON yet. A refusal is not proof the world has "
                       "closed."),
        }
    if isinstance(error, PermissionError):
        return {
            "kind": DENIED,
            "text": "",
            "detail": ("The port opened and RCON would not accept the admin password, "
                       "so the command never ran. The password this cluster was "
                       "launched with and the one in Settings have come apart."),
        }
    why = str(error or "").strip() or error.__class__.__name__
    return {
        "kind": BROKE,
        "text": "",
        "detail": ("Nothing reached the server: %s. That is the error as it came, "
                   "not a diagnosis." % why),
    }


def redact(text, password):
    """`text` with the admin password taken out of it, wherever it appears.

    A belt to the braces. Nothing here is *meant* to carry the password - the RCON
    client takes it from the store and never returns it, and none of the verdicts above
    quote it - but the console renders exception text from a socket library nobody in
    this repo wrote, and "the page never shows the password" is not a property worth
    holding by inspection of somebody else's error strings.

    Blunt on purpose: every occurrence, whatever it is in the middle of. A short
    password that mangles a line of ListPlayers output is a much better failure than a
    long one printed in full.
    """
    out = str(text if text is not None else "")
    pw = str(password or "")
    return out.replace(pw, "[admin password]") if pw and pw in out else out


# The fields that end up on the page, and so the fields that get scrubbed. Named
# rather than "every string in the dict", because `kind` is read by the renderer to
# choose a colour and an admin password that happens to be the word "answered" must
# not be able to change what the box says.
SHOWN = ("command", "text", "detail")


def result(command, body=None, error=None, timeout=None, password=""):
    """One send, as the page will show it. The only thing a route should build.

    Either `body` (the call returned) or `error` (it raised). Everything the page is
    allowed to print goes through redact() here, in one place, so no later caller can
    add a field that quotes the password by forgetting to.
    """
    out = (verdict_for_error(error, timeout) if error is not None
           else verdict(body))
    out["command"] = str(command or "")
    out["headline"] = HEADLINES[out["kind"]]
    out["level"] = LEVELS[out["kind"]]
    for field in SHOWN:
        out[field] = redact(out.get(field, ""), password)
    return out
