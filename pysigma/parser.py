"""
This parser uses lark to transform the condition strings from signatures into callbacks that
invoke the right sequence of searches into the rule and logic operations.
"""
from typing import Dict, Callable, Union
from pathlib import Path

from .windows_event_logs import prepare_event_log
from .build_alert import callback_buildReport, Alert, check_timeframe
from .exceptions import UnsupportedFeature
from .sigma_scan import analyze_x_of, match_search_id

from lark import Lark, Transformer


# SCRIPT_LOCATION = Path(__file__).resolve().parent


# Grammar defined for the condition strings within the Sigma rules
grammar = '''
        start: pipe_rule 
        %import common.WORD   // imports from terminal library
        %ignore " "           // Disregard spaces in text
        pipe_rule: or_rule ["|" aggregation_expression] 
        or_rule: and_rule (("or"|"OR") and_rule)* 
        and_rule: not_rule (("and"|"AND") not_rule)* 
        not_rule: [not] atom 
        not: "NOT" | "not"
        atom: x_of | search_id | "(" pipe_rule ")"
        search_id: SEARCH_ID 
        x: ALL | NUMBER
        x_of: x OF (THEM | search_pattern)
        search_pattern: /[a-zA-Z_][a-zA-Z0-9*_]*/
        aggregation_expression: aggregation_function "(" [aggregation_field] ")" [ "by" group_field ] comparison_op value 
                              | near_aggregation
        aggregation_function: COUNT | MIN | MAX | AVG | SUM
        near_aggregation: "near" or_rule
        aggregation_field: SEARCH_ID
        group_field: SEARCH_ID
        comparison_op: GT | LT | EQ
        GT: ">" 
        LT: "<"
        EQ: "="
        value: NUMBER
        NUMBER: /[1-9][0-9]*/
        NOT: "NOT"
        SEARCH_ID: /[a-zA-Z_][a-zA-Z0-9_]*/
        ALL: "all"
        OF: "of"
        THEM: "them"
        COUNT: "count"
        MIN: "min"
        MAX: "max"
        AVG: "avg"
        SUM: "sum"
        '''


def check_event(raw_event, rules):
    event = prepare_event_log(raw_event)
    alerts = []
    timed_events = []

    for rule_name, rule_obj in rules.items():
        condition = rule_obj.get_condition()

        if condition(rule_obj, event):
            timeframe = rule_obj.get_timeframe()
            if timeframe is not None:
                check_timeframe(rule_obj, rule_name, timed_events, event, alerts)
            else:
                alert = Alert(rule_name, rule_obj.description, event, rule_obj.level,
                              rule_obj.file_name)
                callback_buildReport(alerts, alert)
    return alerts

#
# def parse_logfiles(*logfiles):
#     """
#     Main function tests every event against every rule in the provided list of files
#     :param logfiles: paths to each logfile
#     :return: dict of filename <-> event-alert tuples
#     """
#     for evt in logfiles:
#         event_logfiles.append(SCRIPT_LOCATION / Path(evt))
#     print()
#
#     file_event_alerts = {}
#
#     for f in event_logfiles:
#         log_dict = load_events(f)
#         try:
#             # handle single event
#             if type(log_dict['Events']['Event']) is list:
#                 events = log_dict['Events']['Event']
#             else:
#                 events = [log_dict['Events']['Event']]
#         except KeyError:
#             raise ValueError("The input file %s does not contain any events or is improperly formatted")
#
#         file_event_alerts[f.name] = []
#
#         for e in events:
#             alerts = check_event(e)
#             if len(alerts) > 0:
#                 file_event_alerts[f.name].append((e, alerts))
#
#     return file_event_alerts


def true_function(*_state):
    return True


def false_function(*_state):
    return False


class FactoryTransformer(Transformer):
    @staticmethod
    def start(args):
        return args[0]

    @staticmethod
    def search_id(args):
        name = args[0].value

        def match_hits(signature, event):
            return match_search_id(signature, event, name)

        return match_hits

    @staticmethod
    def search_pattern(args):
        return args[0].value

    @staticmethod
    def atom(args):
        if not all((callable(_x) for _x in args)):
            raise ValueError(args)
        return args[0]

    @staticmethod
    def not_rule(args):
        negate, value = args
        assert callable(value)
        if negate is None:
            return value

        def _negate(*state):
            return not value(*state)
        return _negate

    @staticmethod
    def and_rule(args):
        if not all((callable(_x) for _x in args)):
            raise ValueError(args)

        if len(args) == 1:
            return args[0]

        def _and_operation(*state):
            for component in args:
                if not component(*state):
                    return False
            return True

        return _and_operation

    @staticmethod
    def or_rule(args):
        if not all((callable(_x) for _x in args)):
            raise ValueError(args)

        if len(args) == 1:
            return args[0]

        def _or_operation(*state):
            for component in args:
                if component(*state):
                    return True
            return False

        return _or_operation

    @staticmethod
    def pipe_rule(args):
        return args[0]

    @staticmethod
    def x_of(args):
        # Load the left side of the X of statement
        count = None
        if args[0].children[0].type == 'NUMBER':
            count = int(args[0].children[0].value)

        # Load the right side of the X of statement
        selector = None
        if isinstance(args[2], str):
            selector = args[2]
        elif args[2].type == 'THEM':
            pass
        else:
            raise ValueError()

        # Create a closure on our
        def _check_of_sections(signature, event):
            return analyze_x_of(signature, event, count, selector)
        return _check_of_sections

    @staticmethod
    def aggregation_expression(args):
        raise UnsupportedFeature("Aggregation expressions not supported.")

    @staticmethod
    def near_aggregation(args):
        raise UnsupportedFeature("Near operation not supported.")


# Create & initialize Lark class instance
factory_parser = Lark(grammar, parser='lalr', transformer=FactoryTransformer(), maybe_placeholders=True)


def prepare_condition(raw_condition: Union[str, list]) -> Callable:
    if isinstance(raw_condition, list):
        raw_condition = '(' + ') or ('.join(raw_condition) + ')'
    return factory_parser.parse(raw_condition)
