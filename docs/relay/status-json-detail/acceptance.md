# status-json-detail: acceptance

What waits until after the build GO, so the build review cannot check it.

- [ ] **Tell the hub the final field names (R10).** Trigger: the build GO. The session that built this sends the
  hub session a peer message giving `excerpt.full` (a string, the first 4,000 characters of the agent's last
  words, line breaks kept) and `owner_action_items` (a list of `{action, at}`, `at` null for a merge seen on
  GitHub), because hordle's pm reads them. If a name changed during review, the message gives the new one.
