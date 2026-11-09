# local_ncsahub — NCSA Hub activity feed (Learn@NCSA)

Records enrolments, unenrolments, course completions and activity completions (Moodle user id, course, time;
no names or emails) in `local_ncsahub_outbox`. On install it backfills past course completions from
`course_completions` (no log tables are read). The front door pulls with `local_ncsahub_get_events(afterid, limit)`
hourly; a daily task deletes rows queued more than 30 days ago. Privacy API implemented.

Install on `learnncsa`: copy this folder to `public_html/moodle/local/ncsahub`, check nothing else is pending
(`php admin/cli/upgrade.php --is-pending` exits 0 before copying), then
`php admin/cli/upgrade.php --non-interactive`. Then `php ~/tmp/xfer/hub_learner_setup.php feed` grants the
service role `local/ncsahub:readoutbox` and adds the function (and `core_enrol_get_enrolled_users`) to the
"NCSA Hub (learner service)". Remove: Site administration → Plugins → Plugins overview → Uninstall.
