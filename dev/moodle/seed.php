<?php
// Seeds the dev Moodle from fixtures/moodle so ids match the fixtures exactly. Synthetic data only.
define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/course/lib.php');
\core\session\manager::set_user(get_admin());

$dir = $argv[1];
$cats = json_decode(file_get_contents("$dir/categories.json"), true);
$courses = json_decode(file_get_contents("$dir/courses.json"), true)['courses'];

// Visible categories from the fixture, then any category only courses mention (those are hidden).
$want = [];
foreach ($cats as $c) { $want[(int)$c['id']] = ['name' => html_entity_decode($c['name']), 'visible' => true]; }
foreach ($courses as $c) {
    $cid = (int)$c['categoryid'];
    if ($cid > 0 && !isset($want[$cid])) { $want[$cid] = ['name' => html_entity_decode($c['categoryname']), 'visible' => false]; }
}
ksort($want);
foreach ($want as $id => $w) {
    if ($id === 1) { continue; } // "Category 1" exists after install
    $cat = core_course_category::create(['name' => $w['name'], 'parent' => 0]);
    if ((int)$cat->id !== $id) { fwrite(STDERR, "warning: category {$w['name']} got id {$cat->id}, fixture says $id\n"); }
    if (!$w['visible']) { $cat->hide(); }
}

$enrol = enrol_get_plugin('self');
usort($courses, fn($a, $b) => $a['id'] <=> $b['id']);
foreach ($courses as $c) {
    if ((int)$c['id'] <= 1) { continue; }
    $course = create_course((object)[
        'fullname' => $c['fullname'], 'shortname' => $c['shortname'], 'category' => (int)$c['categoryid'],
        'summary' => $c['summary'], 'summaryformat' => FORMAT_HTML,
        'visible' => isset($c['visible']) ? (int)$c['visible'] : 1,
    ]);
    if ((int)$course->id !== (int)$c['id']) { fwrite(STDERR, "warning: course {$c['fullname']} got id {$course->id}\n"); }
    // Enable exactly the enrolment methods the fixture lists.
    foreach (enrol_get_instances($course->id, false) as $inst) {
        $on = in_array($inst->enrol, $c['enrollmentmethods'], true);
        $DB->set_field('enrol', 'status', $on ? ENROL_INSTANCE_ENABLED : ENROL_INSTANCE_DISABLED, ['id' => $inst->id]);
    }
    foreach ($c['enrollmentmethods'] as $m) {
        if (!$DB->record_exists('enrol', ['courseid' => $course->id, 'enrol' => $m]) && ($p = enrol_get_plugin($m))) {
            $p->add_instance($course, ['status' => ENROL_INSTANCE_ENABLED]);
        }
    }
    if (!empty($c['overviewfiles'])) {
        $img = imagecreatetruecolor(320, 180);
        imagefill($img, 0, 0, imagecolorallocate($img, 19, 41, 75));
        ob_start(); imagepng($img); $png = ob_get_clean();
        get_file_storage()->create_file_from_string([
            'contextid' => context_course::instance($course->id)->id, 'component' => 'course',
            'filearea' => 'overviewfiles', 'itemid' => 0, 'filepath' => '/', 'filename' => 'tile.png',
        ], $png);
    }
    echo "seeded course {$course->id}: {$c['fullname']}\n";
}
