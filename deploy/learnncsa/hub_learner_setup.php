<?php
// NCSA Hub learner features on Learn@NCSA. Idempotent. Usage: php hub_learner_setup.php report|progress|enrol
// report  : read-only counts (no personal data)
// progress: role 15 may read a linked learner's courses + completion; service gets 2 read functions
// enrol   : role 15 may enrol a user as student via the manual method; service gets enrol_manual_enrol_users
// stats   : service gets core_enrol_get_enrolled_users (enrolment counts per course; uses the progress capabilities)
// feed    : after local_ncsahub is installed: role 15 may read its outbox; service gets local_ncsahub_get_events
//           and core_enrol_get_enrolled_users
define('CLI_SCRIPT', true);
require('/home/learnncsa/public_html/moodle/config.php');
require_once($CFG->dirroot . '/webservice/lib.php');
\core\session\manager::set_user(get_admin());
$stage = $argv[1] ?? 'report';
$sys = context_system::instance();
$rid = $DB->get_field('role', 'id', ['shortname' => 'ncsahub_lookup'], MUST_EXIST);
$student = $DB->get_field('role', 'id', ['shortname' => 'student'], MUST_EXIST);
$svc = $DB->get_record('external_services', ['shortname' => 'ncsahub_lookup'], '*', MUST_EXIST);
$ws = new webservice();

if ($stage === 'report') {
    $vis = $DB->get_records_sql("SELECT c.id, c.enablecompletion FROM {course} c JOIN {course_categories} cc ON cc.id = c.category
                                  WHERE c.visible = 1 AND cc.visible = 1 AND c.id <> ?", [SITEID]);
    $n = ['visible_courses' => count($vis), 'completion_enabled' => 0, 'manual_enabled' => 0, 'self_enabled' => 0,
          'self_open_no_key' => 0, 'self_with_key' => 0, 'self_cohort_only' => 0, 'self_newenrols_off' => 0,
          'guest_enabled' => 0, 'open_self_and_manual' => 0];
    foreach ($vis as $c) {
        $n['completion_enabled'] += (int)$c->enablecompletion;
        $inst = enrol_get_instances($c->id, true);
        $man = false; $open = false;
        foreach ($inst as $i) {
            if ($i->enrol === 'manual') { $n['manual_enabled']++; $man = true; }
            if ($i->enrol === 'guest') { $n['guest_enabled']++; }
            if ($i->enrol === 'self') {
                $n['self_enabled']++;
                if ($i->password) { $n['self_with_key']++; }
                elseif ($i->customint5) { $n['self_cohort_only']++; }
                elseif (!$i->customint6) { $n['self_newenrols_off']++; }
                else { $n['self_open_no_key']++; $open = true; }
            }
        }
        if ($open && $man) { $n['open_self_and_manual']++; }
    }
    $n['student_role_id'] = (int)$student;
    $n['hiddenuserfields_has_lastaccess'] = in_array('lastaccess', explode(',', (string)($CFG->hiddenuserfields ?? ''))) ? 1 : 0;
    $n['completion_site_enabled'] = (int)!empty($CFG->enablecompletion);
    $n['service_functions'] = implode(',', $DB->get_fieldset_select('external_services_functions', 'functionname',
        'externalserviceid = ?', [$svc->id]));
    foreach ($n as $k => $v) { echo "$k: $v\n"; }
    exit(0);
}

if ($stage === 'progress') {
    $caps = ['moodle/course:view', 'moodle/course:viewparticipants', 'report/completion:view',
             'moodle/course:viewhiddenuserfields', 'moodle/site:accessallgroups'];
    $fns = ['core_enrol_get_users_courses', 'core_enrol_get_course_enrolment_methods'];
    $desc = 'NCSA Hub (trainingdesktop): look up a signed-in learner\'s account, read their courses and progress.';
} elseif ($stage === 'enrol') {
    $caps = ['enrol/manual:enrol', 'moodle/role:assign'];
    $fns = ['enrol_manual_enrol_users'];
    core_role_set_assign_allowed($rid, $student);
    $desc = 'NCSA Hub (trainingdesktop): look up a signed-in learner, read their courses and progress, enrol them as student in open courses.';
} elseif ($stage === 'feed') {
    if (!get_capability_info('local/ncsahub:readoutbox')) {
        fwrite(STDERR, "local_ncsahub is not installed yet\n"); exit(1);
    }
    $caps = ['local/ncsahub:readoutbox'];
    $fns = ['core_enrol_get_enrolled_users', 'local_ncsahub_get_events'];
    $desc = 'NCSA Hub (trainingdesktop): look up a signed-in learner, read their courses and progress, enrol them as student in open courses, count enrolments per course, read the activity feed (local_ncsahub).';
} elseif ($stage === 'stats') {
    $caps = [];
    $fns = ['core_enrol_get_enrolled_users'];
    $desc = 'NCSA Hub (trainingdesktop): look up a signed-in learner, read their courses and progress, enrol them as student in open courses, count enrolments per course.';
} else {
    fwrite(STDERR, "unknown stage\n"); exit(1);
}
foreach ($caps as $cap) { assign_capability($cap, CAP_ALLOW, $rid, $sys->id, true); }
$DB->set_field('role', 'name', 'NCSA Hub learner service', ['id' => $rid]);
$DB->set_field('role', 'description', $desc, ['id' => $rid]);
foreach ($fns as $f) {
    if (!$ws->service_function_exists($f, $svc->id)) { $ws->add_external_function_to_service($f, $svc->id); }
}
$DB->set_field('external_services', 'name', 'NCSA Hub (learner service)', ['id' => $svc->id]);
accesslib_clear_all_caches(true);
purge_caches();
echo "stage $stage done; role caps now: " . implode(',', $DB->get_fieldset_select('role_capabilities', 'capability',
    'roleid = ? AND contextid = ? AND permission = ?', [$rid, $sys->id, CAP_ALLOW])) . "\n";
echo "service functions: " . implode(',', $DB->get_fieldset_select('external_services_functions', 'functionname',
    'externalserviceid = ?', [$svc->id])) . "\n";
if ($stage === 'enrol') { echo "student assignable by role: " . ($DB->record_exists('role_allow_assign', ['roleid' => $rid, 'allowassign' => $student]) ? 'yes' : 'no') . "\n"; }
