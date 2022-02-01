"""This module contains a Clusterer that clusters events using a heuristic approach."""

from logging import Logger, DEBUG

from typing import List
from multiprocessing import current_process

from logprep.processor.clusterer.signature_calculation.signature_phase import (
    SignaturePhaseStreaming, LogRecord, SignatureEngine)
from logprep.processor.base.processor import RuleBasedProcessor
from logprep.processor.base.exceptions import InvalidRuleDefinitionError, InvalidRuleFileError

from logprep.processor.clusterer.rule import ClustererRule

from logprep.util.processor_stats import ProcessorStats
from logprep.util.time_measurement import TimeMeasurement


class Clusterer(RuleBasedProcessor):
    """Cluster log events using a heuristic."""

    def __init__(self, name: str, tree_config: str, logger: Logger,
                 output_field_name='cluster_signature'):
        super().__init__(name, tree_config, logger)
        self.ps = ProcessorStats()

        self._name = name
        self._rules = []
        self._events_processed = 0

        self.sps = SignaturePhaseStreaming()
        self._output_field_name = output_field_name

        self.has_custom_tests = True

    def describe(self) -> str:
        return f'Clusterer ({self._name})'

    # pylint: disable=W0221
    def add_rules_from_directory(self, rules_dirs):
        for rules_dir in rules_dirs:
            rule_paths = sorted(self._list_json_files_in_directory(rules_dir))
            for rule_path in rule_paths:
                rules = self._load_rules_from_file(rule_path)
                for rule in rules:
                    self._rules.append(rule)
        if self._logger.isEnabledFor(DEBUG):
            self._logger.debug('{} loaded {} rules ({})'.format(self.describe(), len(self._rules),
                                                                current_process().name))
        self.ps.setup_rules(self._rules)
    # pylint: enable=W0221

    def _load_rules_from_file(self, path):
        try:
            return ClustererRule.create_rules_from_file(path)
        except InvalidRuleDefinitionError as error:
            raise InvalidRuleFileError(self._name, path) from error

    @TimeMeasurement.measure_time('clusterer')
    def process(self, event: dict):
        self._events_processed += 1
        self.ps.update_processed_count(self._events_processed)

        if self._is_clusterable(event):
            matching_rules = list()
            for rule in self._rules:
                if rule.matches(event):
                    matching_rules.append(rule)
            self._cluster(event, matching_rules)

    def events_processed_count(self) -> int:
        return self._events_processed

    def _is_clusterable(self, event: dict):
        # The following blocks have not been extracted into functions for performance reasons
        # A message can only be clustered if it exists, despite any other condition
        if 'message' not in event:
            return False
        if event['message'] is None:
            return False

        # Return clusterable state if it exists, since it can be true or false
        if 'clusterable' in event:
            return event['clusterable']

        # Alternatively, check for a clusterable tag
        if 'tags' in event and 'clusterable' in event['tags']:
            return True

        # It is clusterable if a syslog with PRI exists even if no clusterable field exists
        # has_facility = 'syslog' in event and 'facility' in event['syslog']
        # has_severity = 'event' in event and 'severity' in event['event']
        if self._syslog_has_pri(event):
            return True

        return False

    @staticmethod
    def _syslog_has_pri(event: dict):
        return ('syslog' in event and
                'facility' in event['syslog'] and
                'event' in event and
                'severity' in event['event'])

    def _cluster(self, event: dict, rules: List[ClustererRule]):
        cluster_signature_based_on_message = self.sps.run(LogRecord(raw_text=event['message']),
                                                          rules)
        if self._syslog_has_pri(event):
            cluster_signature = ' , '.join([str(event['syslog']['facility']),
                                            str(event['event']['severity']),
                                            cluster_signature_based_on_message])
        else:
            cluster_signature = cluster_signature_based_on_message
        event[self._output_field_name] = cluster_signature

    def test_rules(self):
        results = {}
        for idx, rule in enumerate(self._rules):
            rule_repr = rule.__repr__()
            results[rule_repr] = []
            try:
                for test in rule.tests:
                    result = SignatureEngine.apply_signature_rule(rule, test['raw'])
                    expected_result = test['result']
                    results[rule_repr].append((result, expected_result))
            except AttributeError:
                results[rule_repr].append(None)
        return results