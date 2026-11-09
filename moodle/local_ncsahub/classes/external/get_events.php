<?php
namespace local_ncsahub\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * Pull outbox rows with id > afterid, oldest first.
 */
class get_events extends external_api {
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'afterid' => new external_value(PARAM_INT, 'Return events with an outbox id greater than this', VALUE_DEFAULT, 0),
            'limit' => new external_value(PARAM_INT, 'Maximum events to return (1-5000)', VALUE_DEFAULT, 1000),
        ]);
    }

    public static function execute(int $afterid = 0, int $limit = 1000): array {
        global $DB;
        $params = self::validate_parameters(self::execute_parameters(), ['afterid' => $afterid, 'limit' => $limit]);
        $context = \context_system::instance();
        self::validate_context($context);
        require_capability('local/ncsahub:readoutbox', $context);
        $limit = max(1, min(5000, (int)$params['limit']));
        $rows = $DB->get_records_select('local_ncsahub_outbox', 'id > ?', [(int)$params['afterid']], 'id ASC',
            'id, kind, userid, courseid, cmid, state, timecreated', 0, $limit + 1);
        $more = count($rows) > $limit;
        $rows = array_slice(array_values($rows), 0, $limit);
        $events = array_map(fn($r) => ['id' => (int)$r->id, 'kind' => $r->kind, 'userid' => (int)$r->userid,
            'courseid' => (int)$r->courseid, 'cmid' => (int)$r->cmid, 'state' => (int)$r->state,
            'timecreated' => (int)$r->timecreated], $rows);
        $last = $events ? end($events)['id'] : (int)$params['afterid'];
        return ['events' => $events, 'lastid' => $last, 'more' => $more];
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'events' => new external_multiple_structure(new external_single_structure([
                'id' => new external_value(PARAM_INT, 'Outbox id'),
                'kind' => new external_value(PARAM_ALPHAEXT, 'enrolled, unenrolled, course_completed, activity_completion'),
                'userid' => new external_value(PARAM_INT, 'Moodle user id'),
                'courseid' => new external_value(PARAM_INT, 'Course id'),
                'cmid' => new external_value(PARAM_INT, 'Course module id (activity completion only)'),
                'state' => new external_value(PARAM_INT, 'Activity completion state'),
                'timecreated' => new external_value(PARAM_INT, 'Unix time it happened'),
            ])),
            'lastid' => new external_value(PARAM_INT, 'Highest id returned (pass as afterid next time)'),
            'more' => new external_value(PARAM_BOOL, 'More events are waiting'),
        ]);
    }
}
