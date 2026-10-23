"""The lead developer's approval words: the one word list, the one normalizer and the one matcher (Python 3.13+).

This is a pure text function. It never reads a repository, a packet or a conversation, so it
cannot tell who typed a reply or whether it refers to the seal on screen: the worker still
verifies that provenance before it passes the raw reply here.
"""
import argparse
import json
import string
import sys
import unicodedata

# The one list. A reply is approving only when, whole, it normalizes to exactly one of these.
WORDS = ('GO', 'APPROVE', 'APPROVED', 'PROCEED', 'DOIT')

# The words as the sealed review shows them: lowercase, spaced as a person types them. A test pins that each
# one normalizes to the matching entry of WORDS, so the display can never drift from the list.
DISPLAY = ('go', 'approve', 'approved', 'proceed', 'do it')

# What an approving reply means depends on what is presented, never on the word itself.
PHASES = {'preview': 'stage-and-seal', 'sealed': 'approve'}

# A question is never an answer, so stripping must not turn "Go?" into an approval.
QUESTION_MARKS = ('?', '¿', ';', '؟', '﹖', '？')


def normalize(reply):
    """Uppercase the whole reply and drop every whitespace and punctuation character.

    Returns None when anything else is left that is not an ASCII letter or digit, such as a
    look-alike letter or an emoji, because a reply the tool cannot read exactly is no match.
    """
    kept = []
    for char in reply:
        if char.isspace() or char in string.punctuation or unicodedata.category(char).startswith('P'):
            continue
        if not (char.isascii() and char.isalnum()):
            return None
        kept.append(char.upper())
    return ''.join(kept)


def approves(reply):
    """True when the whole raw reply is exactly one approval word and asks no question."""
    if not isinstance(reply, str) or any(mark in reply for mark in QUESTION_MARKS):
        return False
    return normalize(reply) in WORDS


def meaning(phase, reply):
    """What an approving reply means at this phase, or None when the reply is not a match."""
    if phase not in PHASES:
        raise ValueError('Unknown approval phase: ' + repr(phase))
    return PHASES[phase] if approves(reply) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check a raw lead developer reply against the approval words.')
    parser.add_argument('command', choices=['match'])
    parser.add_argument('--phase', required=True, choices=sorted(PHASES))
    parser.add_argument('--reply', help='The raw reply; read from standard input when omitted')
    args = parser.parse_args(argv)
    reply = args.reply if args.reply is not None else sys.stdin.buffer.read().decode('utf-8-sig')
    result = meaning(args.phase, reply)
    print(json.dumps({'phase': args.phase, 'matched': result is not None, 'meaning': result,
                      'normalized': normalize(reply)}))
    return 0 if result is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
