// Synthetic Pydantic-valid transport shapes, never trusted venue evidence.
export const nativeOutcomeFixture = {
  "contract_version": "blofin-experiment-outcome/v1",
  "instrument": null,
  "entry_binding_hash": null,
  "exit_lineage_hash": null,
  "native_exit_order_ids": [
    "synthetic-exit"
  ],
  "monetary_methodology_version": "synthetic-contract-only/v1",
  "provider": "blofin_native_activity",
  "venue": "BLOFIN",
  "environment": "demo",
  "source": "blofin_demo",
  "organization_id": "00000000-0000-4000-8000-000000000001",
  "execution_account_id": "00000000-0000-4000-8000-000000000001",
  "native_uid": "synthetic-contract-uid",
  "version_id": "00000000-0000-4000-8000-000000000001",
  "strategy_version_id": "00000000-0000-4000-8000-000000000001",
  "variant_key": "baseline",
  "source_record_id": "00000000-0000-4000-8000-000000000001",
  "status": "available",
  "reason": null,
  "proof": {
    "organization_id": "00000000-0000-4000-8000-000000000001",
    "execution_account_id": "00000000-0000-4000-8000-000000000001",
    "native_uid": "synthetic-contract-uid",
    "source": "blofin_demo",
    "version_id": "00000000-0000-4000-8000-000000000001",
    "configuration_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "sample_group_id": "00000000-0000-4000-8000-000000000001",
    "strategy_version_id": "00000000-0000-4000-8000-000000000001",
    "variant_key": "baseline",
    "kind": "closed_trade",
    "source_record_id": "00000000-0000-4000-8000-000000000001",
    "source_content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "authority_origin": "experiment",
    "ownership_command_id": "00000000-0000-4000-8000-000000000001",
    "native_entry_order_id": "synthetic-entry",
    "native_exit_order_id": "synthetic-exit",
    "opened_at": "2026-10-11T10:59:40Z",
    "completed_at": "2026-10-11T10:59:50Z",
    "lineage_methodology_version": "experiment-native-lineage/v1",
    "native_exit_order_ids": [
      "synthetic-exit"
    ],
    "native_entry_fill_ids": [
      "synthetic-entry-fill"
    ],
    "native_exit_fill_ids": [
      "synthetic-exit-fill"
    ]
  },
  "facts": [
    {
      "kind": "fill",
      "native_id": "synthetic-exit-fill",
      "content_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "event_time": "2026-10-11T10:59:50Z",
      "first_observed_at": "2026-10-11T10:59:55Z",
      "instrument": "BTC-USDT",
      "quantity": "0.123456789123456789",
      "quantity_unit": "contracts",
      "price": "10.012345678912345678",
      "fee": "0.1",
      "fee_currency": "USDT",
      "realized_pnl": "1.2"
    }
  ],
  "evaluated_at": "2026-10-11T11:00:00Z",
  "identity_verified_at": "2026-10-11T10:59:30Z",
  "coverage": "reconciled_lineage_only",
  "account_history_complete": false,
  "freshness": "fresh",
  "methodology_version": "blofin-native-reconciliation/v1",
  "quantity_unit": "contracts",
  "quantity": "0.123456789123456789",
  "currency": "USDT",
  "reported_realized_pnl": "1.2",
  "fee_cost": "0.1",
  "realized_pnl_after_fees_excluding_funding": "1.1",
  "funding": null,
  "rounding": "18_decimal_places_half_even",
  "limitations": [
    "funding_not_collected",
    "account_history_retention_unverified",
    "current_instrument_metadata_not_historical_conversion_proof"
  ]
} as const;
export const nativePerformanceFixture = {
  "contract_version": "blofin-experiment-performance/v1",
  "organization_id": "00000000-0000-4000-8000-000000000001",
  "version_id": "00000000-0000-4000-8000-000000000001",
  "sample_group_id": "00000000-0000-4000-8000-000000000001",
  "variant_key": "baseline",
  "source": "blofin_demo",
  "native_uid": "synthetic-contract-uid",
  "evaluated_at": "2026-10-11T11:00:00Z",
  "status": "available",
  "reason": null,
  "sample_count": 1,
  "required_sample_count": 1,
  "currency": "USDT",
  "reported_realized_pnl": "1.2",
  "fee_cost": "0.1",
  "realized_pnl_after_fees_excluding_funding": "1.1",
  "win_rate": "1",
  "outcomes": [
    {
      "contract_version": "blofin-experiment-outcome/v1",
      "instrument": null,
      "entry_binding_hash": null,
      "exit_lineage_hash": null,
      "native_exit_order_ids": [
        "synthetic-exit"
      ],
      "monetary_methodology_version": "synthetic-contract-only/v1",
      "provider": "blofin_native_activity",
      "venue": "BLOFIN",
      "environment": "demo",
      "source": "blofin_demo",
      "organization_id": "00000000-0000-4000-8000-000000000001",
      "execution_account_id": "00000000-0000-4000-8000-000000000001",
      "native_uid": "synthetic-contract-uid",
      "version_id": "00000000-0000-4000-8000-000000000001",
      "strategy_version_id": "00000000-0000-4000-8000-000000000001",
      "variant_key": "baseline",
      "source_record_id": "00000000-0000-4000-8000-000000000001",
      "status": "available",
      "reason": null,
      "proof": {
        "organization_id": "00000000-0000-4000-8000-000000000001",
        "execution_account_id": "00000000-0000-4000-8000-000000000001",
        "native_uid": "synthetic-contract-uid",
        "source": "blofin_demo",
        "version_id": "00000000-0000-4000-8000-000000000001",
        "configuration_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sample_group_id": "00000000-0000-4000-8000-000000000001",
        "strategy_version_id": "00000000-0000-4000-8000-000000000001",
        "variant_key": "baseline",
        "kind": "closed_trade",
        "source_record_id": "00000000-0000-4000-8000-000000000001",
        "source_content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "authority_origin": "experiment",
        "ownership_command_id": "00000000-0000-4000-8000-000000000001",
        "native_entry_order_id": "synthetic-entry",
        "native_exit_order_id": "synthetic-exit",
        "opened_at": "2026-10-11T10:59:40Z",
        "completed_at": "2026-10-11T10:59:50Z",
        "lineage_methodology_version": "experiment-native-lineage/v1",
        "native_exit_order_ids": [
          "synthetic-exit"
        ],
        "native_entry_fill_ids": [
          "synthetic-entry-fill"
        ],
        "native_exit_fill_ids": [
          "synthetic-exit-fill"
        ]
      },
      "facts": [
        {
          "kind": "fill",
          "native_id": "synthetic-exit-fill",
          "content_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
          "event_time": "2026-10-11T10:59:50Z",
          "first_observed_at": "2026-10-11T10:59:55Z",
          "instrument": "BTC-USDT",
          "quantity": "0.123456789123456789",
          "quantity_unit": "contracts",
          "price": "10.012345678912345678",
          "fee": "0.1",
          "fee_currency": "USDT",
          "realized_pnl": "1.2"
        }
      ],
      "evaluated_at": "2026-10-11T11:00:00Z",
      "identity_verified_at": "2026-10-11T10:59:30Z",
      "coverage": "reconciled_lineage_only",
      "account_history_complete": false,
      "freshness": "fresh",
      "methodology_version": "blofin-native-reconciliation/v1",
      "quantity_unit": "contracts",
      "quantity": "0.123456789123456789",
      "currency": "USDT",
      "reported_realized_pnl": "1.2",
      "fee_cost": "0.1",
      "realized_pnl_after_fees_excluding_funding": "1.1",
      "funding": null,
      "rounding": "18_decimal_places_half_even",
      "limitations": [
        "funding_not_collected",
        "account_history_retention_unverified",
        "current_instrument_metadata_not_historical_conversion_proof"
      ]
    }
  ],
  "funding": null,
  "account_history_complete": false,
  "methodology_version": "attributable-native-outcomes/v1",
  "rounding": "18_decimal_places_half_even"
} as const;
