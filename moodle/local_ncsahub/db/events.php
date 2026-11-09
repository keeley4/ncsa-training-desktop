<?php
defined('MOODLE_INTERNAL') || die();

$observers = [
    ['eventname' => '\core\event\user_enrolment_created', 'callback' => '\local_ncsahub\observer::enrolled'],
    ['eventname' => '\core\event\user_enrolment_deleted', 'callback' => '\local_ncsahub\observer::unenrolled'],
    ['eventname' => '\core\event\course_completed', 'callback' => '\local_ncsahub\observer::course_completed'],
    ['eventname' => '\core\event\course_module_completion_updated',
        'callback' => '\local_ncsahub\observer::activity_completion'],
];
