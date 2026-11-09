<?php
defined('MOODLE_INTERNAL') || die();

$tasks = [
    ['classname' => '\local_ncsahub\task\prune_outbox', 'blocking' => 0, 'minute' => '23', 'hour' => '3',
        'day' => '*', 'month' => '*', 'dayofweek' => '*'],
];
