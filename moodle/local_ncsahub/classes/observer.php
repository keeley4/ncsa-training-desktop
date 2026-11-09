<?php
namespace local_ncsahub;

defined('MOODLE_INTERNAL') || die();

/**
 * Event observers. Each writes one small outbox row; failures never interrupt the user's action.
 */
class observer {
    private static function queue(string $kind, int $userid, int $courseid, int $cmid, int $state, int $time): void {
        global $DB;
        if ($userid <= 0 || $courseid <= SITEID) {
            return;
        }
        try {
            $DB->insert_record('local_ncsahub_outbox', (object)['kind' => $kind, 'userid' => $userid,
                'courseid' => $courseid, 'cmid' => $cmid, 'state' => $state, 'timecreated' => $time,
                'timequeued' => time()]);
        } catch (\Throwable $e) {
            debugging('local_ncsahub: could not queue event: ' . $e->getMessage(), DEBUG_DEVELOPER);
        }
    }

    public static function enrolled(\core\event\user_enrolment_created $event): void {
        self::queue('enrolled', (int)$event->relateduserid, (int)$event->courseid, 0, 1, (int)$event->timecreated);
    }

    public static function unenrolled(\core\event\user_enrolment_deleted $event): void {
        self::queue('unenrolled', (int)$event->relateduserid, (int)$event->courseid, 0, 1, (int)$event->timecreated);
    }

    public static function course_completed(\core\event\course_completed $event): void {
        self::queue('course_completed', (int)$event->relateduserid, (int)$event->courseid, 0, 1,
            (int)$event->timecreated);
    }

    public static function activity_completion(\core\event\course_module_completion_updated $event): void {
        $state = (int)($event->other['completionstate'] ?? 0);
        self::queue('activity_completion', (int)$event->relateduserid, (int)$event->courseid,
            (int)$event->contextinstanceid, $state, (int)$event->timecreated);
    }
}
