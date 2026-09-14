"""Redis Stream keys.

One place so producers and consumers can't drift.
"""

EVENTS_MATCH = "specter:events:match"
EVENTS_STREAM_STATUS = "specter:events:stream_status"
EVENTS_ENROLLMENT = "specter:events:enrollment"
JOBS_ENROLL = "specter:jobs:enroll"

ENROLL_GROUP = "enrollers"
