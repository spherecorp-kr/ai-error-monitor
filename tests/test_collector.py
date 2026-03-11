"""Tests for error collector."""
import json
import pytest
from unittest.mock import patch, MagicMock

from lambdas.shared.models import ErrorEntry


class TestErrorEntry:
    def test_fingerprint_consistency(self):
        """Same error should produce same fingerprint."""
        e1 = ErrorEntry(
            timestamp="2026-03-11T00:00:00Z",
            service="appointment-service",
            environment="prod",
            level="ERROR",
            message="NullPointerException: Cannot invoke method",
            stack_trace="at global.drcall.appointment.AppointmentService.confirm(AppointmentService.java:142)",
        )
        e2 = ErrorEntry(
            timestamp="2026-03-11T01:00:00Z",  # different time
            service="appointment-service",
            environment="prod",
            level="ERROR",
            message="NullPointerException: Cannot invoke method",
            stack_trace="at global.drcall.appointment.AppointmentService.confirm(AppointmentService.java:142)",
        )
        assert e1.fingerprint == e2.fingerprint

    def test_fingerprint_differs_for_different_errors(self):
        """Different errors should produce different fingerprints."""
        e1 = ErrorEntry(
            timestamp="2026-03-11T00:00:00Z",
            service="appointment-service",
            environment="prod",
            level="ERROR",
            message="NullPointerException: Cannot invoke method",
            stack_trace="at global.drcall.appointment.AppointmentService.confirm(AppointmentService.java:142)",
        )
        e2 = ErrorEntry(
            timestamp="2026-03-11T00:00:00Z",
            service="payment-service",
            environment="prod",
            level="ERROR",
            message="TimeoutException: Connection timed out",
            stack_trace="at global.drcall.payment.PaymentService.charge(PaymentService.java:89)",
        )
        assert e1.fingerprint != e2.fingerprint

    def test_to_dict_includes_fingerprint(self):
        e = ErrorEntry(
            timestamp="2026-03-11T00:00:00Z",
            service="test",
            environment="dev",
            level="ERROR",
            message="test error",
        )
        d = e.to_dict()
        assert "fingerprint" in d
        assert d["service"] == "test"


class TestCollectorHandler:
    @patch("lambdas.collector.handler._load_targets")
    @patch("lambdas.collector.handler._collect_errors_for_target")
    @patch("lambdas.collector.handler.deduplicate_errors")
    @patch("lambdas.collector.handler._send_to_sqs")
    def test_handler_flow(self, mock_sqs, mock_dedup, mock_collect, mock_targets):
        """Test the main handler orchestration."""
        from lambdas.collector.handler import handler

        mock_target = MagicMock()
        mock_target.name = "test"
        mock_targets.return_value = [mock_target]

        error = ErrorEntry(
            timestamp="now", service="test", environment="dev",
            level="ERROR", message="test error"
        )
        mock_collect.return_value = [error]
        mock_dedup.return_value = [error]
        mock_sqs.return_value = 1

        result = handler({}, None)

        assert result["statusCode"] == 200
        assert result["body"]["total_collected"] == 1
        assert result["body"]["new_errors"] == 1
