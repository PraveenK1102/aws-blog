"""Config-validation tests for the ingestion DLQ plan (§16). Offline, no AWS."""
import unittest

import ingestion_dlq_plan as P


class DlqDefinitionTests(unittest.TestCase):
    def test_dlq_name_is_exact(self):
        self.assertEqual(P.DLQ_NAME, "multitenant-ingestion-dlq.fifo")

    def test_dlq_must_be_fifo(self):
        self.assertTrue(P.DLQ_NAME.endswith(".fifo"))
        self.assertEqual(P.DLQ_ATTRIBUTES["FifoQueue"], "true")

    def test_dlq_retention_is_14_days(self):
        self.assertEqual(P.DLQ_ATTRIBUTES["MessageRetentionPeriod"], str(14 * 24 * 3600))
        self.assertEqual(int(P.DLQ_ATTRIBUTES["MessageRetentionPeriod"]), 1209600)

    def test_dlq_retention_is_longer_than_the_source(self):
        """Evidence must outlive the source queue's own retention."""
        self.assertGreater(int(P.DLQ_ATTRIBUTES["MessageRetentionPeriod"]),
                           P.UNCHANGED["SourceMessageRetentionPeriod"])


class RedrivePolicyTests(unittest.TestCase):
    def test_max_receive_count_is_5(self):
        self.assertEqual(P.MAX_RECEIVE_COUNT, 5)
        self.assertEqual(P.SOURCE_REDRIVE_POLICY["maxReceiveCount"], 5)

    def test_dead_letter_target_is_the_exact_dlq_arn(self):
        self.assertEqual(P.SOURCE_REDRIVE_POLICY["deadLetterTargetArn"], P.DLQ_ARN)
        self.assertTrue(P.DLQ_ARN.endswith(":multitenant-ingestion-dlq.fifo"))

    def test_arns_are_exact_and_same_account_and_region(self):
        for arn in (P.SOURCE_QUEUE_ARN, P.DLQ_ARN):
            self.assertTrue(arn.startswith(f"arn:aws:sqs:{P.REGION}:{P.ACCOUNT}:"), arn)

    def test_source_and_dlq_are_different_queues(self):
        self.assertNotEqual(P.SOURCE_QUEUE_ARN, P.DLQ_ARN)


class RedriveAllowPolicyTests(unittest.TestCase):
    def test_permission_is_by_queue(self):
        self.assertEqual(P.DLQ_REDRIVE_ALLOW_POLICY["redrivePermission"], "byQueue")

    def test_exactly_one_source_queue_is_allowed(self):
        arns = P.DLQ_REDRIVE_ALLOW_POLICY["sourceQueueArns"]
        self.assertEqual(len(arns), 1)
        self.assertEqual(arns[0], P.SOURCE_QUEUE_ARN)

    def test_no_wildcard_source(self):
        for a in P.DLQ_REDRIVE_ALLOW_POLICY["sourceQueueArns"]:
            self.assertNotIn("*", a)


class EventSourceMappingTests(unittest.TestCase):
    def test_planned_concurrency_is_exactly_two(self):
        self.assertEqual(P.PLANNED_MAXIMUM_CONCURRENCY, 2)

    def test_batch_size_stays_one(self):
        self.assertEqual(P.UNCHANGED["BatchSize"], 1)

    def test_report_batch_item_failures_is_not_enabled(self):
        self.assertEqual(P.UNCHANGED["FunctionResponseTypes"], [])

    def test_source_retention_and_visibility_unchanged(self):
        self.assertEqual(P.UNCHANGED["SourceMessageRetentionPeriod"], 345600)
        self.assertEqual(P.UNCHANGED["SourceVisibilityTimeout"], 300)

    def test_worker_timeout_unchanged(self):
        self.assertEqual(P.UNCHANGED["WorkerTimeout"], 300)

    def test_visibility_timeout_is_not_below_worker_timeout(self):
        """A visibility timeout under the worker timeout would redeliver a message
        that is still being processed, inflating the receive count unfairly."""
        self.assertGreaterEqual(P.UNCHANGED["SourceVisibilityTimeout"],
                                P.UNCHANGED["WorkerTimeout"])


class AlarmDefinitionTests(unittest.TestCase):
    def test_two_alarms_defined(self):
        self.assertEqual(len(P.ALARMS), 2)

    def test_dlq_alarm_fires_on_a_single_message(self):
        a = P.ALARMS[0]
        self.assertEqual(a["MetricName"], "ApproximateNumberOfMessagesVisible")
        self.assertEqual(a["Dimensions"][0]["Value"], P.DLQ_NAME)
        self.assertEqual(a["Threshold"], 1)
        self.assertEqual(a["ComparisonOperator"], "GreaterThanOrEqualToThreshold")

    def test_age_alarm_is_15_minutes_on_the_source(self):
        a = P.ALARMS[1]
        self.assertEqual(a["MetricName"], "ApproximateAgeOfOldestMessage")
        self.assertEqual(a["Dimensions"][0]["Value"], P.SOURCE_QUEUE_NAME)
        self.assertEqual(a["Threshold"], 900)
        self.assertEqual(a["ComparisonOperator"], "GreaterThanThreshold")

    def test_alarms_use_a_60_second_period(self):
        for a in P.ALARMS:
            self.assertEqual(a["Period"], 60)
            self.assertEqual(a["EvaluationPeriods"], 1)

    def test_alarm_actions_point_at_the_existing_sns_topic(self):
        """Approved and attached 2026-08-27. No new topic was created."""
        for a in P.ALARMS:
            self.assertEqual(a["AlarmActions"], [P.EXISTING_SNS_TOPIC_ARN])
            self.assertTrue(a["AlarmActions"][0].endswith(":blog-alarms"))

    def test_no_second_sns_topic_is_introduced(self):
        arns = {a for al in P.ALARMS for a in al["AlarmActions"]}
        self.assertEqual(len(arns), 1)


class AppliedStateTests(unittest.TestCase):
    def test_plan_is_marked_applied(self):
        self.assertTrue(P.APPLIED)

    def test_dlq_url_matches_the_dlq_name(self):
        self.assertTrue(P.DLQ_URL.endswith("/" + P.DLQ_NAME))


class RedriveDocumentationTests(unittest.TestCase):
    """§5 correction: the earlier doc wrongly implied a mandatory wait."""

    def setUp(self):
        import os
        repo = os.path.abspath(os.path.join(os.path.dirname(P.__file__), "..", ".."))
        with open(os.path.join(repo, "INGESTION-DLQ-REDRIVE-PLAN.md"),
                  encoding="utf-8") as fh:
            self.doc = fh.read()
        # Markdown emphasis markers must not decide whether an assertion passes.
        self.plain = self.doc.replace("**", "").replace("*", "")

    def test_no_mandatory_five_minute_wait_is_claimed(self):
        """The earlier draft told operators to wait 5 minutes before redriving.
        That was wrong; no variant of it may survive anywhere in the document."""
        for bad in ("Wait out the 5-minute dedup window",
                    "wait out the 5-minute", "wait out the window",
                    "Always wait five minutes"):
            self.assertNotIn(bad, self.plain)
        self.assertIn("not a mandatory waiting period", self.plain)

    def test_ordering_across_redrive_is_not_claimed(self):
        self.assertIn("must NOT be claimed", self.plain)

    def test_idempotency_justification_is_recorded(self):
        self.assertIn("idempotent", self.plain)

    def test_retry_layers_are_distinguished(self):
        self.assertIn("SQS delivery budget", self.plain)
        self.assertIn("SDK-internal", self.plain)

    def test_age_alarm_is_not_described_as_proof_of_a_poison_message(self):
        self.assertIn("does not by itself prove", self.plain)
        self.assertIn("backlog by design", self.plain)


class MutationPlanTests(unittest.TestCase):
    def test_plan_is_ordered_and_creates_the_dlq_before_pointing_at_it(self):
        steps = P.proposed_mutations()
        self.assertEqual([s["step"] for s in steps], [1, 2, 3, 4, 5])
        create = next(s for s in steps if s["action"] == "sqs:CreateQueue")
        point = next(s for s in steps if "RedrivePolicy" in str(s["attributes"]))
        self.assertLess(create["step"], point["step"])

    def test_every_step_records_how_to_reverse_it(self):
        for s in P.proposed_mutations():
            self.assertTrue(s["reversible"], s)

    def test_plan_contains_no_delete_or_purge_action(self):
        for s in P.proposed_mutations():
            self.assertNotIn("Delete", s["action"])
            self.assertNotIn("Purge", s["action"])

    def test_module_performs_no_aws_call(self):
        """Plan-only: importing must not create a client or mutate anything."""
        import ast
        import os
        src = open(os.path.join(os.path.dirname(P.__file__),
                                "ingestion_dlq_plan.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        imports = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
        self.assertNotIn("boto3", imports)
        for banned in ("create_queue", "set_queue_attributes", "put_metric_alarm",
                       "update_event_source_mapping", "delete_queue"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
