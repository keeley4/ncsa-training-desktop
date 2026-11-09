<?php
// NCSA Hub activity feed: records enrolments and completions in a self-pruning outbox that the
// NCSA training front door (trainingdesktop.web.illinois.edu) pulls through a web service.
defined('MOODLE_INTERNAL') || die();

$plugin->component = 'local_ncsahub';
$plugin->version   = 2026110900;
$plugin->requires  = 2024100700; // Moodle 4.5.
$plugin->maturity  = MATURITY_BETA;
$plugin->release   = '0.1.0';
