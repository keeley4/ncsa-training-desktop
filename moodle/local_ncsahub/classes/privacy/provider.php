<?php
namespace local_ncsahub\privacy;

defined('MOODLE_INTERNAL') || die();

use core_privacy\local\metadata\collection;
use core_privacy\local\request\approved_contextlist;
use core_privacy\local\request\approved_userlist;
use core_privacy\local\request\contextlist;
use core_privacy\local\request\userlist;
use core_privacy\local\request\writer;

class provider implements
        \core_privacy\local\metadata\provider,
        \core_privacy\local\request\plugin\provider,
        \core_privacy\local\request\core_userlist_provider {

    public static function get_metadata(collection $collection): collection {
        $collection->add_database_table('local_ncsahub_outbox', [
            'userid' => 'privacy:metadata:outbox:userid',
            'courseid' => 'privacy:metadata:outbox:courseid',
            'kind' => 'privacy:metadata:outbox:kind',
            'timecreated' => 'privacy:metadata:outbox:timecreated',
        ], 'privacy:metadata:outbox');
        $collection->add_external_location_link('ncsahub_frontdoor', [
            'userid' => 'privacy:metadata:outbox:userid',
            'courseid' => 'privacy:metadata:outbox:courseid',
            'kind' => 'privacy:metadata:outbox:kind',
        ], 'privacy:metadata:frontdoor');
        return $collection;
    }

    public static function get_contexts_for_userid(int $userid): contextlist {
        global $DB;
        $list = new contextlist();
        if ($DB->record_exists('local_ncsahub_outbox', ['userid' => $userid])) {
            $list->add_system_context();
        }
        return $list;
    }

    public static function get_users_in_context(userlist $userlist) {
        if ($userlist->get_context()->contextlevel == CONTEXT_SYSTEM) {
            $userlist->add_from_sql('userid', 'SELECT DISTINCT userid FROM {local_ncsahub_outbox}', []);
        }
    }

    public static function export_user_data(approved_contextlist $contextlist) {
        global $DB;
        $userid = $contextlist->get_user()->id;
        foreach ($contextlist->get_contexts() as $context) {
            if ($context->contextlevel != CONTEXT_SYSTEM) {
                continue;
            }
            $rows = $DB->get_records('local_ncsahub_outbox', ['userid' => $userid], 'id', 'id, kind, courseid, cmid, state, timecreated');
            if ($rows) {
                writer::with_context($context)->export_data([get_string('pluginname', 'local_ncsahub')],
                    (object)['events' => array_values($rows)]);
            }
        }
    }

    public static function delete_data_for_all_users_in_context(\context $context) {
        global $DB;
        if ($context->contextlevel == CONTEXT_SYSTEM) {
            $DB->delete_records('local_ncsahub_outbox');
        }
    }

    public static function delete_data_for_user(approved_contextlist $contextlist) {
        global $DB;
        $DB->delete_records('local_ncsahub_outbox', ['userid' => $contextlist->get_user()->id]);
    }

    public static function delete_data_for_users(approved_userlist $userlist) {
        global $DB;
        if ($userlist->get_context()->contextlevel != CONTEXT_SYSTEM || !$userlist->get_userids()) {
            return;
        }
        [$sql, $params] = $DB->get_in_or_equal($userlist->get_userids());
        $DB->delete_records_select('local_ncsahub_outbox', "userid $sql", $params);
    }
}
