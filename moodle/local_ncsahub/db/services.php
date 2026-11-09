<?php
defined('MOODLE_INTERNAL') || die();

// No service is defined here: the function is added to the existing "NCSA Hub (learner service)".
$functions = [
    'local_ncsahub_get_events' => [
        'classname' => '\local_ncsahub\external\get_events',
        'description' => 'Enrolment and completion events after a given outbox id (ids only, no names or emails).',
        'type' => 'read',
        'capabilities' => 'local/ncsahub:readoutbox',
    ],
];
