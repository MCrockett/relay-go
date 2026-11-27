# dispatch

Problem: today the hub passes work to another repo with a peer message, and the receiving session has to remember to run relay new. Work handed over that way is invisible to relay until someone remembers. Examples from 2026-11-27: thinkling PR #19 (clip-hook) and upstream-studio PR #9 (clip-hook-fix).

Owner's words (2026-11-27): "Dispatched tasks like this should go through relay automatically."

Who it is for: the owner and the hub session, which hand tasks to project sessions.

Shape to propose in the spec:
- relay dispatch <repo> "<request>" --relayed, run by a registered hub. It records the request in the target repo, with who asked and the owner's words, and does not make the hub the writer.
- The request shows on relay hub as waiting for a session.
- The target repo's session picks it up with relay take, or turns it into a feature with relay new, and the request text becomes its idea.
- Relay events go back to the hub as usual, and the requesting session is told when the work merges.

Success: a task the hub hands to another repo is recorded by relay at once, shows on the hub until a session picks it up, becomes a feature whose idea is the request, and the hub hears when it merges, with no one having to remember to run relay new.

Context: D1 (delegated merges, PR #41) is already merged into develop.
