<?php
// Same web-service setup as production (see docs/runbook-moodle-ws.md), for the dev Moodle.
// Public-catalog access only: the role has webservice/rest:use and nothing else.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/user/lib.php');
require_once($CFG->dirroot . '/webservice/lib.php');
\core\session\manager::set_user(get_admin());
$sys = context_system::instance();
$tokenfile = $argv[1];

set_config('enablewebservices', 1);
$p = array_filter(explode(',', (string)get_config('core', 'webserviceprotocols')));
if (!in_array('rest', $p)) { $p[] = 'rest'; set_config('webserviceprotocols', implode(',', $p)); }
$a = array_filter(explode(',', (string)$CFG->auth));
if (!in_array('webservice', $a)) { $a[] = 'webservice'; set_config('auth', implode(',', $a)); }

if (!$u = $DB->get_record('user', ['username' => 'ncsahub_ws', 'mnethostid' => $CFG->mnet_localhost_id])) {
    $id = user_create_user((object)['username' => 'ncsahub_ws', 'auth' => 'webservice', 'firstname' => 'NCSA Hub',
        'lastname' => 'Service', 'email' => 'noreply@example.org', 'emailstop' => 1, 'confirmed' => 1,
        'mnethostid' => $CFG->mnet_localhost_id], false, false);
    $u = $DB->get_record('user', ['id' => $id]);
}
if (!$rid = $DB->get_field('role', 'id', ['shortname' => 'ncsahub_ws'])) {
    $rid = create_role('NCSA Hub web service (public catalog)', 'ncsahub_ws', 'Sees only what an anonymous visitor sees.');
    set_role_contextlevels($rid, [CONTEXT_SYSTEM]);
}
assign_capability('webservice/rest:use', CAP_ALLOW, $rid, $sys->id, true);
role_assign($rid, $u->id, $sys->id);

$ws = new webservice();
if (!$s = $DB->get_record('external_services', ['shortname' => 'ncsahub_readonly'])) {
    $sid = $ws->add_external_service((object)['name' => 'NCSA Hub (read-only)', 'shortname' => 'ncsahub_readonly',
        'enabled' => 1, 'restrictedusers' => 1, 'requiredcapability' => '', 'downloadfiles' => 1, 'uploadfiles' => 0]);
    $s = $DB->get_record('external_services', ['id' => $sid]);
}
foreach (['core_webservice_get_site_info', 'core_course_get_categories', 'core_course_get_courses_by_field'] as $f) {
    if (!$ws->service_function_exists($f, $s->id)) { $ws->add_external_function_to_service($f, $s->id); }
}
if (!$DB->record_exists('external_services_users', ['externalserviceid' => $s->id, 'userid' => $u->id])) {
    $ws->add_ws_authorised_user((object)['externalserviceid' => $s->id, 'userid' => $u->id, 'iprestriction' => '', 'validuntil' => 0]);
}
if (!file_exists($tokenfile) || !trim(file_get_contents($tokenfile))) {
    $tt = defined('EXTERNAL_TOKEN_PERMANENT') ? EXTERNAL_TOKEN_PERMANENT : 0;
    $tok = \core_external\util::generate_token($tt, $s, $u->id, $sys, 0, '', 'dev');
    file_put_contents($tokenfile, $tok);
    chmod($tokenfile, 0644); // shared with the api container in the dev stack only
}
purge_caches();
echo "web service ready (token in $tokenfile)\n";
