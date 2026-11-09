<?php
// Calls the new read functions as the ncsahub_lookup account for one username. Prints ids and numbers only.
// Usage: php hub_learner_test.php <moodle-username>
define('CLI_SCRIPT', true);
require('/home/learnncsa/public_html/moodle/config.php');
require_once($CFG->dirroot . '/enrol/externallib.php');
$target = $DB->get_record('user', ['username' => $argv[1], 'deleted' => 0], 'id', MUST_EXIST);
$u = $DB->get_record('user', ['username' => 'ncsahub_lookup'], '*', MUST_EXIST);
\core\session\manager::set_user($u);
$rows = core_enrol_external::get_users_courses($target->id, false);
$rows = \core_external\external_api::clean_returnvalue(core_enrol_external::get_users_courses_returns(), $rows);
$all = count(enrol_get_users_courses($target->id, true));
echo "moodle id {$target->id}: enrolled (active) $all, visible to service " . count($rows) . "\n";
foreach ($rows as $r) {
    printf("  course %d visible=%d progress=%s completed=%s lastaccess=%s\n", $r['id'], $r['visible'],
        var_export($r['progress'] ?? null, true), var_export($r['completed'] ?? null, true), empty($r['lastaccess']) ? '-' : 'yes');
}
$probe = $argv[2] ?? null;
if ($probe) {
    $m = core_enrol_external::get_course_enrolment_methods((int)$probe);
    $m = \core_external\external_api::clean_returnvalue(core_enrol_external::get_course_enrolment_methods_returns(), $m);
    foreach ($m as $i) { printf("  course %d method %s status=%s ws=%s\n", $probe, $i['type'], var_export($i['status'], true), $i['wsfunction'] ?? '-'); }
}
