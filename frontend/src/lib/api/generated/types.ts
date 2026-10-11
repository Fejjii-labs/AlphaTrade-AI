export interface paths {
    "/agent/turns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Run one agent turn */
        post: operations["agent_turn_agent_turns_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/chat/message": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Send chat message */
        post: operations["send_message_chat_message_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/saved/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Capture */
        post: operations["retry_capture_agent_saved_retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/knowledge/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List knowledge documents */
        get: operations["list_documents_knowledge_documents_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/knowledge/chunks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List document chunks */
        get: operations["list_chunks_knowledge_chunks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/knowledge/ingest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ingest plain-text document into the knowledge base */
        post: operations["ingest_document_knowledge_ingest_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/knowledge/documents/{document_id}/retry-indexing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry failed indexing of your document */
        post: operations["retry_indexing_knowledge_documents__document_id__retry_indexing_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/proposals/{proposal_id}/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Confirm one structured agent proposal */
        post: operations["confirm_agent_proposal_agent_proposals__proposal_id__confirm_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/agent/proposals/{proposal_id}/reject": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reject one structured agent proposal */
        post: operations["reject_agent_proposal_agent_proposals__proposal_id__reject_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/strategies/{strategy_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update user strategy */
        patch: operations["update_strategy_strategies__strategy_id__patch"];
        trace?: never;
    };
    "/dashboard/attention": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read-only paper attention queue for the current tenant and user */
        get: operations["attention_queue_dashboard_attention_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/dashboard/daily-review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Recorded Daily Review for the current tenant and user */
        get: operations["daily_review_dashboard_daily_review_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/exchange/blofin/activity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Activity */
        get: operations["activity_exchange_blofin_activity_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listing */
        get: operations["listing_experiments_get"];
        put?: never;
        /** Create */
        post: operations["create_experiments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detail */
        get: operations["detail_experiments__experiment_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Fork */
        post: operations["fork_experiments__experiment_id__versions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/transition": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Transition */
        post: operations["transition_experiments__experiment_id__versions__version_id__transition_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve */
        post: operations["approve_experiments__experiment_id__versions__version_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/promote": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Promote */
        post: operations["promote_experiments__experiment_id__versions__version_id__promote_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Native Outcome */
        get: operations["native_outcome_experiments__experiment_id__versions__version_id__outcomes__source_record_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/performance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Native Performance */
        get: operations["native_performance_experiments__experiment_id__versions__version_id__performance_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/samples": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sample */
        post: operations["sample_experiments__experiment_id__versions__version_id__samples_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listing */
        get: operations["listing_experiments__experiment_id__versions__version_id__trendpulse_screenings_get"];
        put?: never;
        /** Screen */
        post: operations["screen_experiments__experiment_id__versions__version_id__trendpulse_screenings_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/trendpulse-screenings/{record_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detail */
        get: operations["detail_trendpulse_screenings__record_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/strategies/{strategy_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List strategy versions */
        get: operations["list_strategy_versions_strategies__strategy_id__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/execution/accounts/paper": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Paper Account Status */
        get: operations["paper_account_status_execution_accounts_paper_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AccountMode
         * @enum {string}
         */
        AccountMode: "NET";
        /** ActionRequest */
        ActionRequest: {
            /** Arguments */
            arguments?: {
                [key: string]: unknown;
            };
            /** Name */
            name: string;
        };
        /** ActivityCoverage */
        ActivityCoverage: {
            /** Covered Begin Ms */
            covered_begin_ms?: string | null;
            /** Covered End Ms */
            covered_end_ms?: string | null;
            /**
             * Gap Detected
             * @default false
             */
            gap_detected?: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "order" | "fill";
            /** Last Attempt At */
            last_attempt_at?: string | null;
            /** Last Error Code */
            last_error_code?: string | null;
            /** Last Successful Sync */
            last_successful_sync?: string | null;
            /** Native Cursor */
            native_cursor?: string | null;
            /** Next Retry At */
            next_retry_at?: string | null;
            /**
             * Selection
             * @enum {string}
             */
            selection: "cursor_sweep" | "time_window";
            /** Window Begin Ms */
            window_begin_ms?: string | null;
            /**
             * Window Complete
             * @default false
             */
            window_complete?: boolean;
            /** Window End Ms */
            window_end_ms?: string | null;
        };
        /** ActivityItem */
        ActivityItem: {
            /** Average Price */
            average_price?: string | null;
            /** Base Currency */
            base_currency?: string | null;
            /** Client Order Id */
            client_order_id?: string | null;
            /** Command Id */
            command_id?: string | null;
            /** Contract Multiplier */
            contract_multiplier?: string | null;
            /** Contract Type */
            contract_type?: string | null;
            /** Created At Ms */
            created_at_ms?: string | null;
            /** Fee */
            fee?: string | null;
            /** Fee Currency */
            fee_currency?: string | null;
            /** Filled Quantity */
            filled_quantity?: string | null;
            /** Funding */
            funding?: null;
            /** Instrument */
            instrument: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "order" | "fill";
            /** Metadata Observed At */
            metadata_observed_at?: string | null;
            /** Native Id */
            native_id: string;
            /** Occurred At Ms */
            occurred_at_ms: string;
            /** Order Id */
            order_id: string;
            /** Order Type */
            order_type?: string | null;
            /**
             * Origin
             * @enum {string}
             */
            origin: "native" | "alphatrade_matched";
            /** Position Side */
            position_side: string;
            /** Price */
            price?: string | null;
            /** Quantity */
            quantity: string;
            /**
             * Quantity Unit
             * @default contracts
             * @constant
             */
            quantity_unit?: "contracts";
            /** Realized Pnl */
            realized_pnl?: string | null;
            /** Reduce Only */
            reduce_only?: string | null;
            /** Settlement Currency */
            settlement_currency?: string | null;
            /** Side */
            side: string;
            /** State */
            state?: string | null;
            /** Strategy Id */
            strategy_id?: string | null;
            /** Trade Id */
            trade_id?: string | null;
            /** Updated At Ms */
            updated_at_ms?: string | null;
        };
        /** ActivityPage */
        ActivityPage: {
            /** Account Uid */
            account_uid: string;
            /** Coverage */
            coverage: components["schemas"]["ActivityCoverage"][];
            /**
             * Environment
             * @default demo
             * @constant
             */
            environment?: "demo";
            /**
             * Freshness
             * @enum {string}
             */
            freshness: "fresh" | "stale" | "never_synced" | "unverified";
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Identity Error Code */
            identity_error_code?: string | null;
            /**
             * Identity Status
             * @enum {string}
             */
            identity_status: "verified" | "unverified";
            /** Identity Verified At */
            identity_verified_at?: string | null;
            /** Items */
            items: components["schemas"]["ActivityItem"][];
            /** Limitations */
            limitations: string[];
            /** Next Cursor */
            next_cursor?: string | null;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /**
             * Partial Coverage
             * @default true
             * @constant
             */
            partial_coverage?: true;
            /**
             * Schema Version
             * @default BloFinActivityV1
             * @constant
             */
            schema_version?: "BloFinActivityV1";
            /**
             * Venue
             * @default BLOFIN
             * @constant
             */
            venue?: "BLOFIN";
        };
        /**
         * AgentCapability
         * @description User-facing capabilities. One primary capability is chosen per turn.
         * @enum {string}
         */
        AgentCapability: "general_conversation" | "market_and_portfolio" | "strategy_brain" | "strategy_analytics" | "governed_learning" | "strategy_retrieval" | "strategy_authoring" | "pattern_and_rule_capture" | "trade_discussion" | "pre_trade_reasoning" | "journal_capture" | "post_trade_reflection" | "knowledge_retrieval" | "statistics_and_performance" | "screenshot_analysis" | "voice_io" | "persistent_context" | "daily_review";
        /**
         * AgentMessageResponse
         * @description Structured agent response (Slice 9).
         */
        AgentMessageResponse: {
            analysis?: components["schemas"]["TradingAnalysisDetail"] | null;
            /** Approval Id */
            approval_id?: string | null;
            /** Approval Reason */
            approval_reason?: string | null;
            /**
             * Approval Required
             * @default false
             */
            approval_required?: boolean;
            /**
             * Approval Status
             * @description pending | not_required | blocked
             */
            approval_status: string;
            /** Citations */
            citations?: components["schemas"]["Citation"][];
            /** Confidence */
            confidence?: number | null;
            /** Conversation Id */
            conversation_id: string;
            /**
             * History Injected
             * @default 0
             */
            history_injected?: number;
            /** Limitations */
            limitations?: string[];
            narrative?: components["schemas"]["TradingNarrativeDetail"] | null;
            narrative_meta?: components["schemas"]["NarrativeMetadata"] | null;
            paper_execution?: components["schemas"]["AgentPaperResult"] | null;
            pending_proposal?: components["schemas"]["StrategyProposalRecord"] | null;
            /** Proposal Id */
            proposal_id?: string | null;
            /** Reply */
            reply: string;
            /** Request Id */
            request_id: string;
            risk_level?: components["schemas"]["RiskSeverity"] | null;
            risk_result?: components["schemas"]["RiskCheckResult"] | null;
            /** Tool Outputs */
            tool_outputs?: components["schemas"]["ToolOutput"][];
            usage?: components["schemas"]["UsageEvent"] | null;
        };
        /** AgentPaperResult */
        AgentPaperResult: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Authorization Id */
            authorization_id?: string | null;
            /**
             * Candidate Id
             * Format: uuid
             */
            candidate_id: string;
            /** Confirmation Message */
            confirmation_message: string;
            /**
             * Eligibility Id
             * Format: uuid
             */
            eligibility_id: string;
            /** Journal Trade Id */
            journal_trade_id?: string | null;
            /** Paper Action Id */
            paper_action_id?: string | null;
            plan: components["schemas"]["TradePlanRevision"];
            pretrade: components["schemas"]["PaperPreTradeAnalysis"];
            /** Reason Code */
            reason_code?: string | null;
            /** Receipt Id */
            receipt_id?: string | null;
            /**
             * Replayed
             * @default false
             */
            replayed?: boolean;
            risk_result: components["schemas"]["RiskCheckResult"];
            /**
             * Stage
             * @enum {string}
             */
            stage: "proposed" | "executed" | "blocked";
        };
        /** AgentTurnRequest */
        AgentTurnRequest: {
            action?: components["schemas"]["ActionRequest"] | null;
            analytics_filters?: components["schemas"]["StrategyAnalyticsFilters"] | null;
            /** Conversation Id */
            conversation_id?: string | null;
            /** Message */
            message: string;
            /** Source Document Id */
            source_document_id?: string | null;
            /** Strategy Id */
            strategy_id?: string | null;
            /** Symbol */
            symbol?: string | null;
            /** Timeframe */
            timeframe?: string | null;
        };
        /** AgentTurnResult */
        AgentTurnResult: {
            /** Artifact Kinds */
            artifact_kinds: components["schemas"]["ArtifactKind"][];
            /**
             * Assistant Message Id
             * Format: uuid
             */
            assistant_message_id: string;
            /**
             * Authority Mutated
             * @default false
             */
            authority_mutated?: boolean;
            capability: components["schemas"]["AgentCapability"];
            /** Capture Clarification */
            capture_clarification?: string | null;
            /** Capture Error */
            capture_error?: string | null;
            /** Capture Source Message Id */
            capture_source_message_id?: string | null;
            /**
             * Capture Status
             * @default not_needed
             * @enum {string}
             */
            capture_status?: "not_needed" | "saved" | "failed" | "clarification" | "unavailable";
            /** Connections */
            connections?: components["schemas"]["ConnectionRef"][];
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            daily_review?: components["schemas"]["DailyReview"] | null;
            /**
             * Execution Attempted
             * @default false
             * @constant
             */
            execution_attempted?: false;
            /** Full Reply */
            full_reply?: string | null;
            /** Governed Learning */
            governed_learning?: components["schemas"]["GovernedLearningStatus"][];
            /** Knowledge */
            knowledge?: components["schemas"]["KnowledgeHit"][];
            /** Limitations */
            limitations?: string[];
            market_quote?: components["schemas"]["MarketQuoteView"] | null;
            /** Model Usage */
            model_usage?: {
                [key: string]: unknown;
            }[];
            operation: components["schemas"]["TurnOperation"];
            paper_safety: components["schemas"]["PaperSafetyContract"];
            /** Portfolio Summary */
            portfolio_summary?: string | null;
            /** Prior User Messages */
            prior_user_messages?: string[];
            /** Proposals */
            proposals?: components["schemas"]["StructuredActionProposal"][];
            /**
             * Real Trading Enabled
             * @default false
             * @constant
             */
            real_trading_enabled?: false;
            /** Recorded Evidence */
            recorded_evidence?: string | null;
            /** Reply */
            reply: string;
            /** Saved Entries */
            saved_entries?: components["schemas"]["SavedEntry"][];
            /**
             * Schema Version
             * @default InteractiveAgent/v1
             * @constant
             */
            schema_version?: "InteractiveAgent/v1";
            screenshot?: components["schemas"]["ScreenshotAnalysisContract"] | null;
            /** Statistics Summary */
            statistics_summary?: string | null;
            /** Strategies */
            strategies?: components["schemas"]["StrategyHit"][];
            /** Strategy Analytics */
            strategy_analytics?: components["schemas"]["StrategyAnalyticsReport"][];
            /**
             * User Message Id
             * Format: uuid
             */
            user_message_id: string;
            voice?: components["schemas"]["VoiceIoContract"] | null;
        };
        /**
         * ArtifactKind
         * @description Explicit record types. Free-form text does not collapse these together.
         * @enum {string}
         */
        ArtifactKind: "observation" | "hypothesis" | "strategy" | "rule" | "journal_entry" | "trade_decision" | "lesson";
        /**
         * AttentionCategory
         * @enum {string}
         */
        AttentionCategory: "risk_block" | "risk_event" | "provider_outage" | "missing_evidence" | "stale_evidence" | "telegram_delivery_failure" | "watcher_state" | "confirmed_setup" | "forming_setup" | "paper_position" | "strategy_proposal_pending" | "strategy_validation_job" | "replay_result" | "daily_review_lesson";
        /** AttentionItem */
        AttentionItem: {
            /**
             * Acknowledgement State
             * @default unsupported
             * @enum {string}
             */
            acknowledgement_state?: "unsupported" | "unacknowledged" | "acknowledged";
            category: components["schemas"]["AttentionCategory"];
            /** Expires At */
            expires_at?: string | null;
            /**
             * Item Id
             * Format: uuid
             */
            item_id: string;
            /** Reason */
            reason: string;
            /** Recommended Next Action */
            recommended_next_action?: string | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "critical" | "high" | "medium" | "low" | "info";
            /** Sources */
            sources: components["schemas"]["ReviewSource"][];
            /** Strategy Id */
            strategy_id?: string | null;
            /** Strategy Version Id */
            strategy_version_id?: string | null;
            /** Symbol */
            symbol?: string | null;
            /** Title */
            title: string;
        };
        /** AttentionQueue */
        AttentionQueue: {
            /**
             * Approves Strategies
             * @default false
             * @constant
             */
            approves_strategies?: false;
            /**
             * Bypasses Risk
             * @default false
             * @constant
             */
            bypasses_risk?: false;
            /**
             * Executes Trades
             * @default false
             * @constant
             */
            executes_trades?: false;
            /**
             * Execution Mode
             * @default paper
             * @constant
             */
            execution_mode?: "paper";
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Items */
            items: components["schemas"]["AttentionItem"][];
            /** Limitations */
            limitations: string[];
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Recommended Next Action */
            recommended_next_action: string | null;
            /**
             * Schema Version
             * @default AttentionQueue/v1
             * @constant
             */
            schema_version?: "AttentionQueue/v1";
            /**
             * Telegram Delivery
             * @default false
             * @constant
             */
            telegram_delivery?: false;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * AuthorizationChannel
         * @enum {string}
         */
        AuthorizationChannel: "WEB" | "API" | "TELEGRAM";
        /**
         * BacktestStatus
         * @description Placeholder backtest lifecycle.
         * @enum {string}
         */
        BacktestStatus: "not_run" | "not_started" | "scheduled" | "queued" | "running" | "complete" | "completed" | "failed";
        /** BasisPolicy */
        BasisPolicy: {
            evidence_price: components["schemas"]["SemanticAmount"];
            execution_price: components["schemas"]["SemanticAmount"];
            /** Formula */
            formula: string;
            /** Freshness Seconds */
            freshness_seconds: number;
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
            /** Tolerance Bps */
            tolerance_bps: string;
        };
        /** BookLevel */
        BookLevel: {
            /** Base Quantity */
            base_quantity: string;
            /** Price */
            price: string;
        };
        /** CalculationInput */
        CalculationInput: {
            /** Conservative Remainder */
            conservative_remainder: string;
            /** Formula Id */
            formula_id: string;
            /** Formula Version */
            formula_version: string;
            /** Input Value */
            input_value: string;
            /** Name */
            name: string;
            /** Precision */
            precision: number;
            /** Result Value */
            result_value: string;
            /** Rounding Mode */
            rounding_mode: string;
            /** Unit */
            unit: string;
        };
        /** CaptureRetry */
        CaptureRetry: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Source Message Id
             * Format: uuid
             */
            source_message_id: string;
        };
        /** ChatMessageRequest */
        ChatMessageRequest: {
            /** Conversation Id */
            conversation_id?: string | null;
            /** Message */
            message: string;
            /** Strategy Id */
            strategy_id?: string | null;
            /** Symbol */
            symbol?: string | null;
            /** Timeframe */
            timeframe?: string | null;
        };
        /**
         * ChunkMetadata
         * @description Filterable metadata attached to a chunk for scoped retrieval.
         */
        ChunkMetadata: {
            /** Page Number */
            page_number?: number | null;
            /** Risk Tag */
            risk_tag?: string | null;
            /** Section Title */
            section_title?: string | null;
            /** Source Filename */
            source_filename?: string | null;
            source_type: components["schemas"]["DocumentSourceType"];
            /** Strategy Tag */
            strategy_tag?: string | null;
            /** Symbol Tag */
            symbol_tag?: string | null;
            /** Timeframe Tag */
            timeframe_tag?: string | null;
            /** Title */
            title?: string | null;
        };
        /**
         * Citation
         * @description A citation returned alongside RAG-grounded answers.
         */
        Citation: {
            /**
             * Chunk Id
             * Format: uuid
             */
            chunk_id: string;
            /** Chunk Ordinal */
            chunk_ordinal?: number | null;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Page Number */
            page_number?: number | null;
            /** Score */
            score?: number | null;
            /** Section Title */
            section_title?: string | null;
            /** Snippet */
            snippet?: string | null;
            /** Source Filename */
            source_filename?: string | null;
            source_type: components["schemas"]["DocumentSourceType"];
            /** Title */
            title?: string | null;
        };
        /** ConnectionRef */
        ConnectionRef: {
            artifact_kind: components["schemas"]["ArtifactKind"];
            provenance: components["schemas"]["ProvenanceSource"];
            /** Record Id */
            record_id: string;
            /** Relation */
            relation: string;
            /** Title */
            title: string;
        };
        /**
         * ContractStyle
         * @enum {string}
         */
        ContractStyle: "linear" | "inverse";
        /**
         * ContractType
         * @enum {string}
         */
        ContractType: "LINEAR" | "INVERSE";
        /**
         * CostSource
         * @description How usage cost was determined — only ``provider_reported`` is billing-grade.
         * @enum {string}
         */
        CostSource: "provider_reported" | "tokenizer_estimated" | "static_estimated" | "unavailable";
        /** DailyPnl */
        DailyPnl: {
            /** Breakeven */
            breakeven: number;
            /** Closed Count */
            closed_count: number;
            /** Cohort */
            cohort: string;
            /** Complete */
            complete: boolean;
            /** Expectancy */
            expectancy: string | null;
            /** Losses */
            losses: number;
            /** Measured Count */
            measured_count: number;
            /** Minimum Sample */
            minimum_sample: number;
            /** Missing Pnl Count */
            missing_pnl_count: number;
            /** Recorded Net Pnl */
            recorded_net_pnl: string | null;
            /** Sources */
            sources: components["schemas"]["ReviewSource"][];
            /** Win Rate */
            win_rate: string | null;
            /** Wins */
            wins: number;
        };
        /** DailyReview */
        DailyReview: {
            /** Content Hash */
            content_hash: string;
            /** Counts */
            counts: {
                [key: string]: number;
            };
            /** Daily Pnl */
            daily_pnl: components["schemas"]["DailyPnl"][];
            /** Facts */
            facts: components["schemas"]["ReviewItem"][];
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Limitations */
            limitations: string[];
            /**
             * Live Executable
             * @default false
             * @constant
             */
            live_executable?: false;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Research Suggestions */
            research_suggestions: components["schemas"]["ReviewItem"][];
            /**
             * Review Id
             * Format: uuid
             */
            review_id: string;
            /**
             * Schema Version
             * @default DailyReview/v1
             * @constant
             */
            schema_version?: "DailyReview/v1";
            /** System Inference */
            system_inference: components["schemas"]["ReviewItem"][];
            /**
             * Telegram Delivery
             * @default false
             * @constant
             */
            telegram_delivery?: false;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
            /** User Observations */
            user_observations: components["schemas"]["ReviewItem"][];
            window: components["schemas"]["ReviewWindow"];
        };
        /**
         * DataCompleteness
         * @enum {string}
         */
        DataCompleteness: "complete" | "partial" | "unknown";
        /**
         * DerivativeMetric
         * @enum {string}
         */
        DerivativeMetric: "open_interest" | "funding";
        /**
         * DerivativeObservation
         * @description Identity includes instrument, venue, market, provider and native timeframe.
         *
         *     Missing event times stay null; observation time never substitutes for them.
         *     Funding is a dimensionless settled rate, without annualization or an assumed
         *     8h interval. OI is never combined across venues or quantity conventions.
         */
        "DerivativeObservation-Output": {
            availability: components["schemas"]["EvidenceAvailability"];
            /** Calculation Method */
            calculation_method: string;
            /** Collected At */
            collected_at?: string | null;
            /** Content Hash */
            content_hash: string;
            /**
             * Coverage Kind
             * @default single_provider_record
             * @constant
             */
            coverage_kind?: "single_provider_record";
            /** Event Time */
            event_time: string | null;
            freshness: components["schemas"]["FreshnessEvaluation-Output"] | null;
            /** Freshness Policy Version */
            freshness_policy_version: string;
            /**
             * Historical Coverage
             * @default false
             * @constant
             */
            historical_coverage?: false;
            identity: components["schemas"]["EvidenceMarketIdentity-Output"];
            /**
             * Methodology Version
             * @default provider-reported-derivatives/v2
             * @constant
             */
            methodology_version?: "provider-reported-derivatives/v2";
            metric: components["schemas"]["DerivativeMetric"];
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Reason */
            reason?: string | null;
            /** Units */
            units: string;
            /** Value */
            value: string | null;
        };
        /** DocumentIngestionMetadata */
        DocumentIngestionMetadata: {
            file?: components["schemas"]["FileProvenance"] | null;
            indexing?: components["schemas"]["IndexingObservation"] | null;
        };
        /**
         * DocumentSourceType
         * @description RAG corpus source types (reconciled with Architecture §7).
         * @enum {string}
         */
        DocumentSourceType: "trading_playbook" | "product_requirements" | "system_architecture" | "risk_policy" | "strategy_template" | "trade_journal" | "review_note" | "mistakes_database" | "general_note";
        /**
         * EntryOrderType
         * @enum {string}
         */
        EntryOrderType: "MARKET" | "LIMIT";
        /**
         * EntryRuleBlock
         * @description Structured entry trigger.
         */
        "EntryRuleBlock-Output": {
            /** Conditions */
            conditions?: components["schemas"]["RuleCondition-Output"][];
            /** @default long */
            direction?: components["schemas"]["TradeDirection"];
            /** Notes */
            notes?: string | null;
            trigger_type: components["schemas"]["EntryTriggerType"];
        };
        /**
         * EntrySide
         * @enum {string}
         */
        EntrySide: "BUY" | "SELL";
        /**
         * EntryTriggerType
         * @description Machine-testable entry trigger types (Slice 36).
         * @enum {string}
         */
        EntryTriggerType: "ema_pullback" | "breakout" | "liquidity_sweep" | "reclaim" | "failed_breakout" | "rsi_threshold" | "volume_confirmation" | "trend_alignment";
        /**
         * EvidenceAvailability
         * @enum {string}
         */
        EvidenceAvailability: "AVAILABLE" | "MISSING" | "STALE" | "UNSUPPORTED" | "INCOMPLETE";
        /**
         * EvidenceMarketIdentity
         * @description Full identity required on every first-slice evidence object.
         */
        "EvidenceMarketIdentity-Output": {
            instrument: components["schemas"]["InstrumentIdentity-Output"];
            market_type: components["schemas"]["app__market_contracts__enums__MarketType"];
            provenance: components["schemas"]["ProviderProvenance"];
            source: components["schemas"]["SourceIdentity"];
            timeframe?: components["schemas"]["Timeframe"] | null;
            venue: components["schemas"]["VenueId"];
        };
        /**
         * ExitRuleBlock
         * @description Structured exit rule.
         */
        "ExitRuleBlock-Output": {
            /** Conditions */
            conditions?: components["schemas"]["RuleCondition-Output"][];
            /** Notes */
            notes?: string | null;
            /** R Multiple */
            r_multiple?: string | null;
            rule_type: components["schemas"]["ExitRuleType"];
            /** Size Fraction */
            size_fraction?: number | null;
            /** Value */
            value?: string | null;
        };
        /**
         * ExitRuleType
         * @description Machine-testable exit rule blocks.
         * @enum {string}
         */
        ExitRuleType: "fixed_stop" | "atr_stop" | "swing_stop" | "tp_multiple" | "tp_price_levels" | "partial_tp" | "runner_structure_break";
        /** ExitTarget */
        ExitTarget: {
            derivation: components["schemas"]["VersionedDerivation"];
            /** Order */
            order: number;
            price: components["schemas"]["SemanticAmount"];
            /** Quantity Fraction */
            quantity_fraction: string;
        };
        /** ExperimentAccount */
        ExperimentAccount: {
            /**
             * Execution Account Id
             * Format: uuid
             */
            execution_account_id: string;
            /** Execution Identity Audit Id */
            execution_identity_audit_id?: string | null;
            /** Native Uid */
            native_uid?: string | null;
            source: components["schemas"]["ExperimentSource"];
        };
        /** ExperimentApproval */
        ExperimentApproval: {
            /**
             * Authorized Until
             * Format: date-time
             */
            authorized_until: string;
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Confirm
             * @constant
             */
            confirm: "APPROVE_BOUNDED_EXPERIMENT";
            /** Expected Revision */
            expected_revision: number;
        };
        /** ExperimentBoundTrendPulse */
        ExperimentBoundTrendPulse: {
            /**
             * Account Verified Here
             * @default false
             * @constant
             */
            account_verified_here?: false;
            /**
             * Binding Id
             * Format: uuid
             */
            binding_id: string;
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Contract Version
             * @default trendpulse-experiment-research/v1
             * @constant
             */
            contract_version?: "trendpulse-experiment-research/v1";
            /**
             * Declared Execution Account Id
             * Format: uuid
             */
            declared_execution_account_id: string;
            declared_execution_source: components["schemas"]["ExperimentSource"];
            /** Declared Native Uid */
            declared_native_uid: string | null;
            /**
             * Experiment Id
             * Format: uuid
             */
            experiment_id: string;
            /**
             * Experiment Version Id
             * Format: uuid
             */
            experiment_version_id: string;
            /**
             * Native Execution
             * @default false
             * @constant
             */
            native_execution?: false;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Performance */
            performance?: null;
            /**
             * Runtime Activated
             * @default false
             * @constant
             */
            runtime_activated?: false;
            /**
             * Sample Eligible
             * @default false
             * @constant
             */
            sample_eligible?: false;
            /**
             * Sample Group Id
             * Format: uuid
             */
            sample_group_id: string;
            signal: components["schemas"]["TrendPulseSignal"];
            /** Strategy Content Hash */
            strategy_content_hash: string;
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Variant Key */
            variant_key: string;
        };
        /** ExperimentConfiguration */
        "ExperimentConfiguration-Input": {
            account: components["schemas"]["ExperimentAccount"];
            /**
             * Contract Version
             * @default experiment-config/v1
             * @constant
             */
            contract_version?: "experiment-config/v1";
            family: components["schemas"]["ExperimentFamily"];
            mode: components["schemas"]["ExperimentMode"];
            model_policy: components["schemas"]["ExperimentModelPolicy-Input"];
            risk_limits: components["schemas"]["ExperimentRiskLimits-Input"];
            sample_target: components["schemas"]["ExperimentSampleTarget"];
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Symbols */
            symbols: string[];
            /** Timeframes */
            timeframes: components["schemas"]["Timeframe"][];
            /** Variants */
            variants: components["schemas"]["ExperimentVariant"][];
        };
        /** ExperimentConfiguration */
        "ExperimentConfiguration-Output": {
            account: components["schemas"]["ExperimentAccount"];
            /**
             * Contract Version
             * @default experiment-config/v1
             * @constant
             */
            contract_version?: "experiment-config/v1";
            family: components["schemas"]["ExperimentFamily"];
            mode: components["schemas"]["ExperimentMode"];
            model_policy: components["schemas"]["ExperimentModelPolicy-Output"];
            risk_limits: components["schemas"]["ExperimentRiskLimits-Output"];
            sample_target: components["schemas"]["ExperimentSampleTarget"];
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Symbols */
            symbols: string[];
            /** Timeframes */
            timeframes: components["schemas"]["Timeframe"][];
            /** Variants */
            variants: components["schemas"]["ExperimentVariant"][];
        };
        /** ExperimentCreate */
        ExperimentCreate: {
            configuration: components["schemas"]["ExperimentConfiguration-Input"];
            /** Idempotency Key */
            idempotency_key: string;
            /** Name */
            name: string;
        };
        /** ExperimentDetail */
        ExperimentDetail: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Versions */
            versions: components["schemas"]["ExperimentVersion"][];
        };
        /**
         * ExperimentFamily
         * @enum {string}
         */
        ExperimentFamily: "operational_nested_continuation/v1" | "swing_failure_pattern/v1" | "trendpulse_1r/v1";
        /**
         * ExperimentMode
         * @enum {string}
         */
        ExperimentMode: "exploration" | "validation";
        /** ExperimentModelPolicy */
        "ExperimentModelPolicy-Input": {
            /**
             * Max Calls
             * @default 0
             */
            max_calls?: number;
            /**
             * Max Cost Usd
             * @default 0
             */
            max_cost_usd?: number | string;
            /**
             * Max Tokens
             * @default 0
             */
            max_tokens?: number;
            /**
             * Mode
             * @default disabled
             * @enum {string}
             */
            mode?: "disabled" | "advisory";
            /** Model */
            model?: string | null;
            /** Provider */
            provider?: string | null;
        };
        /** ExperimentModelPolicy */
        "ExperimentModelPolicy-Output": {
            /**
             * Max Calls
             * @default 0
             */
            max_calls?: number;
            /**
             * Max Cost Usd
             * @default 0
             */
            max_cost_usd?: string;
            /**
             * Max Tokens
             * @default 0
             */
            max_tokens?: number;
            /**
             * Mode
             * @default disabled
             * @enum {string}
             */
            mode?: "disabled" | "advisory";
            /** Model */
            model?: string | null;
            /** Provider */
            provider?: string | null;
        };
        /** ExperimentPage */
        ExperimentPage: {
            /** Items */
            items: components["schemas"]["ExperimentDetail"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** ExperimentPerformance */
        ExperimentPerformance: {
            /**
             * Account History Complete
             * @default false
             * @constant
             */
            account_history_complete?: false;
            /**
             * Contract Version
             * @default blofin-experiment-performance/v1
             * @constant
             */
            contract_version?: "blofin-experiment-performance/v1";
            /** Currency */
            currency?: string | null;
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Fee Cost */
            fee_cost?: string | null;
            /** Funding */
            funding?: null;
            /**
             * Methodology Version
             * @default attributable-native-outcomes/v1
             * @constant
             */
            methodology_version?: "attributable-native-outcomes/v1";
            /** Native Uid */
            native_uid: string | null;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /**
             * Outcomes
             * @default []
             */
            outcomes?: components["schemas"]["NativeOutcome"][];
            /** Realized Pnl After Fees Excluding Funding */
            realized_pnl_after_fees_excluding_funding?: string | null;
            /** Reason */
            reason?: string | null;
            /** Reported Realized Pnl */
            reported_realized_pnl?: string | null;
            /** Required Sample Count */
            required_sample_count: number;
            /**
             * Rounding
             * @default 18_decimal_places_half_even
             * @constant
             */
            rounding?: "18_decimal_places_half_even";
            /** Sample Count */
            sample_count: number;
            /**
             * Sample Group Id
             * Format: uuid
             */
            sample_group_id: string;
            /**
             * Source
             * @default blofin_demo
             * @constant
             */
            source?: "blofin_demo";
            /**
             * Status
             * @enum {string}
             */
            status: "available" | "unavailable";
            /** Variant Key */
            variant_key: string;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
            /** Win Rate */
            win_rate?: string | null;
        } & (unknown & unknown & unknown);
        /** ExperimentPromotion */
        ExperimentPromotion: {
            /** Expected Revision */
            expected_revision: number;
            /** Variant Key */
            variant_key: string;
        };
        /** ExperimentRiskLimits */
        "ExperimentRiskLimits-Input": {
            /** Cost Allowance */
            cost_allowance: number | string;
            /** Max Daily Loss */
            max_daily_loss: number | string;
            /** Max Drawdown */
            max_drawdown: number | string;
            /** Max Leverage */
            max_leverage: number | string;
            /** Max Open Positions */
            max_open_positions: number;
            /** Max Position Notional */
            max_position_notional: number | string;
            /** Max Risk Per Trade */
            max_risk_per_trade: number | string;
            /** Max Total Exposure */
            max_total_exposure: number | string;
            /** Max Trades Per Day */
            max_trades_per_day: number;
            /** Max Trades Total */
            max_trades_total: number;
            /** Max Weekly Loss */
            max_weekly_loss: number | string;
            /**
             * Quote Currency
             * @default USDT
             * @constant
             */
            quote_currency?: "USDT";
        };
        /** ExperimentRiskLimits */
        "ExperimentRiskLimits-Output": {
            /** Cost Allowance */
            cost_allowance: string;
            /** Max Daily Loss */
            max_daily_loss: string;
            /** Max Drawdown */
            max_drawdown: string;
            /** Max Leverage */
            max_leverage: string;
            /** Max Open Positions */
            max_open_positions: number;
            /** Max Position Notional */
            max_position_notional: string;
            /** Max Risk Per Trade */
            max_risk_per_trade: string;
            /** Max Total Exposure */
            max_total_exposure: string;
            /** Max Trades Per Day */
            max_trades_per_day: number;
            /** Max Trades Total */
            max_trades_total: number;
            /** Max Weekly Loss */
            max_weekly_loss: string;
            /**
             * Quote Currency
             * @default USDT
             * @constant
             */
            quote_currency?: "USDT";
        };
        /** ExperimentSample */
        ExperimentSample: {
            /**
             * Completed At
             * Format: date-time
             */
            completed_at: string;
            /** Evidence Hash */
            evidence_hash: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "closed_trade" | "setup_observation";
            /**
             * Opened At
             * Format: date-time
             */
            opened_at: string;
            /**
             * Sample Group Id
             * Format: uuid
             */
            sample_group_id: string;
            source: components["schemas"]["ExperimentSource"];
            /** Source Record Id */
            source_record_id: string;
            /** Variant Key */
            variant_key: string;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
        };
        /** ExperimentSampleCreate */
        ExperimentSampleCreate: {
            /** Source Record Id */
            source_record_id: string;
            /** Variant Key */
            variant_key: string;
        };
        /** ExperimentSampleTarget */
        ExperimentSampleTarget: {
            /**
             * Kind
             * @enum {string}
             */
            kind: "closed_trade" | "setup_observation";
            /** Maximum */
            maximum: number;
            /** Minimum */
            minimum: number;
        };
        /**
         * ExperimentSource
         * @enum {string}
         */
        ExperimentSource: "blofin_demo" | "internal_simulation";
        /**
         * ExperimentState
         * @enum {string}
         */
        ExperimentState: "draft" | "pending_approval" | "approved" | "running" | "paused" | "completed" | "promoted";
        /** ExperimentTransition */
        ExperimentTransition: {
            /**
             * Action
             * @enum {string}
             */
            action: "submit" | "start" | "pause" | "complete";
            /** Expected Revision */
            expected_revision: number;
        };
        /** ExperimentVariant */
        ExperimentVariant: {
            /** Key */
            key: string;
            /** Parameters */
            parameters: {
                [key: string]: unknown;
            };
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
        };
        /** ExperimentVersion */
        ExperimentVersion: {
            /** Approved At */
            approved_at: string | null;
            /** Approved By */
            approved_by: string | null;
            /** Authorized Until */
            authorized_until: string | null;
            /** Completed At */
            completed_at: string | null;
            configuration: components["schemas"]["ExperimentConfiguration-Output"];
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Experiment Id
             * Format: uuid
             */
            experiment_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Parent Version Id */
            parent_version_id: string | null;
            /** Paused At */
            paused_at: string | null;
            /** Performance */
            performance?: null;
            /** Promoted At */
            promoted_at: string | null;
            /** Promotion Version Id */
            promotion_version_id: string | null;
            /** Revision */
            revision: number;
            /**
             * Runtime Activated
             * @default false
             * @constant
             */
            runtime_activated?: false;
            /** Sample Counts */
            sample_counts: {
                [key: string]: number;
            };
            /**
             * Sample Group Id
             * Format: uuid
             */
            sample_group_id: string;
            /** Started At */
            started_at: string | null;
            state: components["schemas"]["ExperimentState"];
            /** Strategy Content Hashes */
            strategy_content_hashes: {
                [key: string]: string;
            };
            /** Submitted At */
            submitted_at: string | null;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
            /** Version */
            version: number;
        };
        /** ExperimentVersionCreate */
        ExperimentVersionCreate: {
            configuration: components["schemas"]["ExperimentConfiguration-Input"];
            /**
             * Parent Version Id
             * Format: uuid
             */
            parent_version_id: string;
        };
        /** FileProvenance */
        FileProvenance: {
            /** Byte Size */
            byte_size: number;
            /**
             * Confirmed At
             * Format: date-time
             */
            confirmed_at: string;
            /** Extracted Characters */
            extracted_characters: number;
            /** Extracted Text Hash */
            extracted_text_hash: string;
            /** Filename */
            filename: string;
            /** Media Type */
            media_type: string;
            /** Parser Version */
            parser_version: string;
            /** Raw Content Hash */
            raw_content_hash: string;
        };
        /**
         * Finality
         * @enum {string}
         */
        Finality: "final" | "forming" | "corrected" | "unknown";
        /** FiveMinuteFlow */
        "FiveMinuteFlow-Output": {
            /** Aggressive Buy Base Volume */
            aggressive_buy_base_volume: string;
            /** Aggressive Buy Quote Volume */
            aggressive_buy_quote_volume: string;
            /** Aggressive Sell Base Volume */
            aggressive_sell_base_volume: string;
            /** Aggressive Sell Quote Volume */
            aggressive_sell_quote_volume: string;
            /** Buy Sell Imbalance Ratio */
            buy_sell_imbalance_ratio: string | null;
            /** Event Time */
            event_time: string | null;
            /** Quote Volume Delta */
            quote_volume_delta: string;
            /** Rolling Cvd */
            rolling_cvd: string;
            /** Rolling Quote Cvd */
            rolling_quote_cvd: string;
            /** Signed Volume Delta */
            signed_volume_delta: string;
            /** Terminal Price */
            terminal_price: string | null;
            /** Trade Count */
            trade_count: number;
            /**
             * Window End
             * Format: date-time
             */
            window_end: string;
            /**
             * Window Start
             * Format: date-time
             */
            window_start: string;
        };
        /** FreshnessEvaluation */
        "FreshnessEvaluation-Output": {
            /** Age Seconds */
            age_seconds: string;
            /** Clock Skew Seconds */
            clock_skew_seconds: string;
            /** Content Hash */
            content_hash: string;
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Policy Version */
            policy_version: string;
            /**
             * Source Time
             * Format: date-time
             */
            source_time: string;
            state: components["schemas"]["FreshnessState"];
            /**
             * Valid Until
             * Format: date-time
             */
            valid_until: string;
        };
        /**
         * FreshnessState
         * @enum {string}
         */
        FreshnessState: "fresh" | "aging" | "stale" | "gap" | "unknown";
        /** GovernedLearningStatus */
        GovernedLearningStatus: {
            /**
             * Approval State
             * @enum {string}
             */
            approval_state: "proposed" | "validating" | "approved" | "rolled_back" | "rejected" | "superseded";
            /**
             * Base Version Id
             * Format: uuid
             */
            base_version_id: string;
            /** Baseline Run Id */
            baseline_run_id?: string | null;
            /** Blockers */
            blockers?: string[];
            /**
             * Can Roll Back
             * @default false
             */
            can_roll_back?: boolean;
            /** Comparison Hash */
            comparison_hash?: string | null;
            /** Content Hash */
            content_hash: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Evidence Ids */
            evidence_ids: components["schemas"]["LearningEvidenceRef"][];
            /**
             * Explicit Evidence Review Required
             * @default true
             */
            explicit_evidence_review_required?: boolean;
            /** Hypothesis */
            hypothesis: string;
            /**
             * Improvement Claim
             * @default false
             * @constant
             */
            improvement_claim?: false;
            /**
             * Insufficient Evidence
             * @default true
             */
            insufficient_evidence?: boolean;
            /**
             * Live Execution Permitted
             * @default false
             * @constant
             */
            live_execution_permitted?: false;
            /** Observed Net Pnl Delta */
            observed_net_pnl_delta?: string | null;
            /** Outperformed Baseline */
            outperformed_baseline?: boolean | null;
            /** Paper Active Version Id */
            paper_active_version_id?: string | null;
            /**
             * Paper Validation Completed
             * @default false
             */
            paper_validation_completed?: boolean;
            /** Paper Validation Run Id */
            paper_validation_run_id?: string | null;
            /**
             * Proposal Id
             * Format: uuid
             */
            proposal_id: string;
            /** Proposed Parameters */
            proposed_parameters: {
                [key: string]: unknown;
            };
            /** Proposed Run Id */
            proposed_run_id?: string | null;
            /** Proposed Version Id */
            proposed_version_id: string | null;
            /** Reason */
            reason: string;
            /**
             * Replayed
             * @default false
             */
            replayed?: boolean;
            /** Rollback Version Id */
            rollback_version_id?: string | null;
            /** Sample Limitations */
            sample_limitations: string[];
            /** Source Observations */
            source_observations: components["schemas"]["LearningEvidenceRef"][];
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /** Validation Plan */
            validation_plan: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** IndexingObservation */
        IndexingObservation: {
            /**
             * Attempts
             * @default 0
             */
            attempts?: number;
            /** Error Code */
            error_code?: string | null;
            /** Fallback Used */
            fallback_used: boolean;
            /** Job Id */
            job_id?: string | null;
            /** Next Attempt At */
            next_attempt_at?: string | null;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Sql Chunk Count */
            sql_chunk_count: number;
            /** Vector Backend */
            vector_backend?: string | null;
            /**
             * Vector Index Status
             * @default unknown
             * @enum {string}
             */
            vector_index_status?: "pending" | "ready" | "failed" | "unknown" | "upsert_acknowledged";
        };
        /**
         * IngestDocumentRequest
         * @description Ingest plain text into the knowledge base.
         */
        IngestDocumentRequest: {
            /** Organization Id */
            organization_id?: string | null;
            /** Risk Tag */
            risk_tag?: string | null;
            source_type: components["schemas"]["DocumentSourceType"];
            /** Source Uri */
            source_uri?: string | null;
            /** Strategy Tag */
            strategy_tag?: string | null;
            /** Symbol Tag */
            symbol_tag?: string | null;
            /** Text */
            text: string;
            /** Timeframe Tag */
            timeframe_tag?: string | null;
            /** Title */
            title: string;
            /** User Id */
            user_id?: string | null;
            /**
             * Version
             * @default 1
             */
            version?: number;
        };
        /**
         * IngestDocumentResponse
         * @description Summary returned after document ingestion.
         */
        IngestDocumentResponse: {
            /** Chunk Count */
            chunk_count: number;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /**
             * Duplicate
             * @default false
             */
            duplicate?: boolean;
            /**
             * Fallback Used
             * @default false
             */
            fallback_used?: boolean;
            /** Source Hash */
            source_hash: string;
            /**
             * Sql Chunks Stored
             * @default true
             */
            sql_chunks_stored?: boolean;
            /**
             * Vector Backend
             * @description Authoritative vector backend used for upsert (e.g. qdrant, in-memory-vector).
             */
            vector_backend?: string | null;
            /**
             * Vector Index Status
             * @default unknown
             * @enum {string}
             */
            vector_index_status?: "pending" | "ready" | "failed" | "upsert_acknowledged" | "unknown";
            /** Version */
            version: number;
        };
        /**
         * InstrumentIdentity
         * @description Canonical perpetual instrument identity; independent of provider symbol formatting.
         */
        "InstrumentIdentity-Output": {
            /** Base Asset */
            base_asset: string;
            /** Base Quantity Unit */
            base_quantity_unit: string;
            /** Contract Multiplier */
            contract_multiplier: string;
            contract_style: components["schemas"]["ContractStyle"];
            /** Instrument Id */
            instrument_id: string;
            market_type: components["schemas"]["app__market_contracts__enums__MarketType"];
            /** Price Unit */
            price_unit: string;
            product_family: components["schemas"]["ProductFamily"];
            /** Provider Symbol */
            provider_symbol: string;
            /** Quote Asset */
            quote_asset: string;
            /** Quote Quantity Unit */
            quote_quantity_unit: string;
            /** Settlement Asset */
            settlement_asset: string;
            venue: components["schemas"]["VenueId"];
        };
        /** InstrumentRules */
        InstrumentRules: {
            /** Base Currency */
            base_currency: string;
            /** Contract Multiplier */
            contract_multiplier: string;
            contract_type: components["schemas"]["ContractType"];
            /** Lot Size */
            lot_size: string;
            /** Minimum Notional */
            minimum_notional: string;
            /** Minimum Quantity */
            minimum_quantity: string;
            /** Quote Currency */
            quote_currency: string;
            /** Rules Version */
            rules_version: string;
            /** Settlement Currency */
            settlement_currency: string;
            /** Tick Size */
            tick_size: string;
        };
        /**
         * JournalStatsWarning
         * @description One confidence/data-coverage warning.
         */
        JournalStatsWarning: {
            code: components["schemas"]["JournalStatsWarningCode"];
            /** Message */
            message: string;
        };
        /**
         * JournalStatsWarningCode
         * @description Machine-readable warning codes attached to statistics results.
         * @enum {string}
         */
        JournalStatsWarningCode: "low_sample" | "no_closed_trades" | "no_decided_trades" | "missing_pnl" | "missing_risk" | "no_losing_trades" | "partial_excursion_data" | "partial_capture_data" | "partial_timing_data" | "partial_missed_profit_data" | "result_truncated";
        /**
         * JournalTradeSource
         * @description Origin of a canonical journal trade (AT-030).
         * @enum {string}
         */
        JournalTradeSource: "manual" | "paper_execution" | "manual_demo_test" | "paper_validation" | "backtest" | "imported" | "system";
        /** KnowledgeHit */
        KnowledgeHit: {
            /**
             * Chunk Id
             * Format: uuid
             */
            chunk_id: string;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Match Count */
            match_count: number;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            provenance: components["schemas"]["ProvenanceSource"];
            /**
             * Retrieval Mode
             * @enum {string}
             */
            retrieval_mode: "lexical_store" | "vector";
            /** Snippet */
            snippet: string;
            /** Source Type */
            source_type: string;
            /** Title */
            title: string;
            /** User Id */
            user_id?: string | null;
        };
        /** LearningEvidenceRef */
        LearningEvidenceRef: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "conversation_message" | "journal_trade" | "journal_observation" | "document" | "backtest_run" | "paper_validation_run";
        };
        /**
         * LessonSourceMetadata
         * @description Provenance when a strategy version is created from an accepted lesson.
         */
        LessonSourceMetadata: {
            /** Accepted Lesson Text */
            accepted_lesson_text: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Lesson Id
             * Format: uuid
             */
            lesson_id: string;
            /** Mistake Type */
            mistake_type: string;
            /** Reviewer Notes */
            reviewer_notes?: string | null;
            /** Rule Update Summary */
            rule_update_summary?: string | null;
        };
        /**
         * MarginMode
         * @enum {string}
         */
        MarginMode: "CROSS" | "ISOLATED";
        /**
         * MarketEvidenceContext
         * @description Stable v1 optional context; required qualification uses canonical evidence.
         *
         *     Event selection can retain an earlier cutoff than evaluation/receipt. Every
         *     available observation still needs a consumer freshness/hash/identity check.
         *     Unsupported capabilities below carry no inferred observations or values.
         */
        MarketEvidenceContext: {
            /** Anchor Symbol */
            anchor_symbol: string;
            /** Anchor Venue */
            anchor_venue: string;
            /**
             * Contract Version
             * @default public-market-context/v1
             * @constant
             */
            contract_version?: "public-market-context/v1";
            /**
             * Cross Venue Components
             * @default []
             */
            cross_venue_components?: string[];
            /**
             * Derivatives
             * @default []
             */
            derivatives?: components["schemas"]["DerivativeObservation-Output"][];
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /** Evidence Cutoff At */
            evidence_cutoff_at?: string | null;
            historical_order_book?: components["schemas"]["UnavailableContextMetric"];
            open_interest_change?: components["schemas"]["UnavailableContextMetric"];
            open_interest_notional?: components["schemas"]["UnavailableContextMetric"];
            order_book?: components["schemas"]["OrderBookObservation"] | null;
            order_flow?: components["schemas"]["OrderFlowObservation-Output"] | null;
            /**
             * Qualification Authority
             * @default false
             * @constant
             */
            qualification_authority?: false;
        };
        /** MarketQuoteView */
        MarketQuoteView: {
            evidence_context?: components["schemas"]["MarketEvidenceContext"] | null;
            /** Fallback Used */
            fallback_used: boolean;
            /** Is Live */
            is_live: boolean;
            /** Is Stale */
            is_stale: boolean;
            /** Last Price */
            last_price: string;
            /** Provider Name */
            provider_name: string;
            /** Source */
            source: string;
            /** Symbol */
            symbol: string;
        };
        /**
         * MarketRegime
         * @description Coarse market-regime label recorded on a journal trade (AT-030).
         * @enum {string}
         */
        MarketRegime: "trending_up" | "trending_down" | "ranging" | "volatile" | "quiet" | "unknown";
        /**
         * NarrativeMetadata
         * @description How the narrative layer was produced (for UI transparency).
         */
        NarrativeMetadata: {
            /**
             * Fallback Used
             * @default false
             */
            fallback_used?: boolean;
            /** Latency Ms */
            latency_ms?: number | null;
            /** Model */
            model: string;
            /** Provider */
            provider: string;
            /**
             * Source
             * @description llm | deterministic_fallback
             */
            source: string;
            /**
             * Validation Passed
             * @default true
             */
            validation_passed?: boolean;
        };
        /**
         * NativeExperimentSourceProof
         * @description Additive stored JSON proof; a11 already stores opaque proof/evidence hashes.
         */
        NativeExperimentSourceProof: {
            /**
             * Authority Origin
             * @constant
             */
            authority_origin: "experiment";
            /**
             * Completed At
             * Format: date-time
             */
            completed_at: string;
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Execution Account Id
             * Format: uuid
             */
            execution_account_id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "closed_trade" | "setup_observation";
            /**
             * Lineage Methodology Version
             * @default experiment-native-lineage/v1
             * @constant
             */
            lineage_methodology_version?: "experiment-native-lineage/v1";
            /** Native Entry Fill Ids */
            native_entry_fill_ids: string[];
            /** Native Entry Order Id */
            native_entry_order_id?: string | null;
            /** Native Exit Fill Ids */
            native_exit_fill_ids: string[];
            /** Native Exit Order Id */
            native_exit_order_id?: string | null;
            /** Native Exit Order Ids */
            native_exit_order_ids: string[];
            /** Native Uid */
            native_uid: string | null;
            /**
             * Opened At
             * Format: date-time
             */
            opened_at: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Ownership Command Id */
            ownership_command_id?: string | null;
            /**
             * Sample Group Id
             * Format: uuid
             */
            sample_group_id: string;
            source: components["schemas"]["ExperimentSource"];
            /** Source Content Hash */
            source_content_hash: string;
            /** Source Record Id */
            source_record_id: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Variant Key */
            variant_key: string;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
        };
        /** NativeFactProvenance */
        NativeFactProvenance: {
            /** Content Hash */
            content_hash: string;
            /**
             * Event Time
             * Format: date-time
             */
            event_time: string;
            /** Fee */
            fee: string | null;
            /** Fee Currency */
            fee_currency: string | null;
            /**
             * First Observed At
             * Format: date-time
             */
            first_observed_at: string;
            /** Instrument */
            instrument: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "order" | "fill";
            /** Native Id */
            native_id: string;
            /** Price */
            price: string | null;
            /** Quantity */
            quantity: string;
            /**
             * Quantity Unit
             * @default contracts
             * @constant
             */
            quantity_unit?: "contracts";
            /** Realized Pnl */
            realized_pnl: string | null;
        };
        /** NativeOutcome */
        NativeOutcome: {
            /**
             * Account History Complete
             * @default false
             * @constant
             */
            account_history_complete?: false;
            /**
             * Contract Version
             * @default blofin-experiment-outcome/v1
             * @constant
             */
            contract_version?: "blofin-experiment-outcome/v1";
            /**
             * Coverage
             * @default incomplete
             * @enum {string}
             */
            coverage?: "reconciled_lineage_only" | "incomplete";
            /** Currency */
            currency?: string | null;
            /** Entry Binding Hash */
            entry_binding_hash?: string | null;
            /**
             * Environment
             * @default demo
             * @constant
             */
            environment?: "demo";
            /**
             * Evaluated At
             * Format: date-time
             */
            evaluated_at: string;
            /**
             * Execution Account Id
             * Format: uuid
             */
            execution_account_id: string;
            /** Exit Lineage Hash */
            exit_lineage_hash?: string | null;
            /**
             * Facts
             * @default []
             */
            facts?: components["schemas"]["NativeFactProvenance"][];
            /** Fee Cost */
            fee_cost?: string | null;
            /**
             * Freshness
             * @default unavailable
             * @enum {string}
             */
            freshness?: "fresh" | "stale" | "unavailable";
            /** Funding */
            funding?: null;
            /** Identity Verified At */
            identity_verified_at?: string | null;
            /** Instrument */
            instrument?: string | null;
            /**
             * Limitations
             * @default [
             *       "funding_not_collected",
             *       "account_history_retention_unverified",
             *       "current_instrument_metadata_not_historical_conversion_proof"
             *     ]
             */
            limitations?: string[];
            /**
             * Methodology Version
             * @default blofin-native-reconciliation/v1
             * @constant
             */
            methodology_version?: "blofin-native-reconciliation/v1";
            /** Monetary Methodology Version */
            monetary_methodology_version?: string | null;
            /**
             * Native Exit Order Ids
             * @default []
             */
            native_exit_order_ids?: string[];
            /** Native Uid */
            native_uid: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            proof?: components["schemas"]["NativeExperimentSourceProof"] | null;
            /**
             * Provider
             * @default blofin_native_activity
             * @constant
             */
            provider?: "blofin_native_activity";
            /** Quantity */
            quantity?: string | null;
            /**
             * Quantity Unit
             * @default contracts
             * @constant
             */
            quantity_unit?: "contracts";
            /** Realized Pnl After Fees Excluding Funding */
            realized_pnl_after_fees_excluding_funding?: string | null;
            /** Reason */
            reason?: string | null;
            /** Reported Realized Pnl */
            reported_realized_pnl?: string | null;
            /**
             * Rounding
             * @default 18_decimal_places_half_even
             * @constant
             */
            rounding?: "18_decimal_places_half_even";
            /**
             * Source
             * @default blofin_demo
             * @constant
             */
            source?: "blofin_demo";
            /** Source Record Id */
            source_record_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "available" | "unavailable";
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Variant Key */
            variant_key: string;
            /**
             * Venue
             * @default BLOFIN
             * @constant
             */
            venue?: "BLOFIN";
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
        } & (unknown & unknown);
        /**
         * NestedMaturityStage
         * @enum {string}
         */
        NestedMaturityStage: "N1" | "N2" | "N3" | "N4_PLUS";
        /**
         * NoTradeRuleBlock
         * @description Structured no-trade filter.
         */
        "NoTradeRuleBlock-Output": {
            /** Conditions */
            conditions?: components["schemas"]["RuleCondition-Output"][];
            /** Notes */
            notes?: string | null;
            rule_type: components["schemas"]["NoTradeRuleType"];
            /** Threshold */
            threshold?: string | null;
        };
        /**
         * NoTradeRuleType
         * @description Machine-testable no-trade filters.
         * @enum {string}
         */
        NoTradeRuleType: "low_volume" | "high_funding" | "weekend_chop" | "daily_loss_lock" | "green_day_protection" | "htf_conflict";
        /** NonNegativeSemanticAmount */
        NonNegativeSemanticAmount: {
            /** Unit */
            unit: string;
            /** Value */
            value: string;
        };
        /**
         * ObservationType
         * @enum {string}
         */
        ObservationType: "ohlcv" | "trade" | "cvd" | "order_book" | "volume" | "structure" | "open_interest" | "funding";
        /**
         * OhlcvBar
         * @description One venue kline after normalization.
         */
        "OhlcvBar-Output": {
            /** Adapter Version */
            adapter_version: string;
            /** Base Volume */
            base_volume: string;
            /** Close */
            close: string;
            /** Content Hash */
            content_hash: string;
            finality: components["schemas"]["Finality"];
            /** High */
            high: string;
            instrument: components["schemas"]["InstrumentIdentity-Output"];
            /**
             * Interval End
             * Format: date-time
             */
            interval_end: string;
            /**
             * Interval Start
             * Format: date-time
             */
            interval_start: string;
            /** Low */
            low: string;
            /** Open */
            open: string;
            /** Provider Complete */
            provider_complete: boolean;
            /** Quote Volume */
            quote_volume: string;
            /** Revision */
            revision: number;
            /** Source Event Id */
            source_event_id: string;
            /**
             * Source Time
             * Format: date-time
             */
            source_time: string;
            timeframe: components["schemas"]["Timeframe"];
            /** Trade Count */
            trade_count?: number | null;
        };
        /** OrderBookObservation */
        OrderBookObservation: {
            /** Ask Base Quantity */
            ask_base_quantity?: string | null;
            /** Ask Quote Notional */
            ask_quote_notional?: string | null;
            /**
             * Asks
             * @default []
             */
            asks?: components["schemas"]["BookLevel"][];
            availability: components["schemas"]["EvidenceAvailability"];
            /** Base Units */
            base_units: string;
            /** Bid Base Quantity */
            bid_base_quantity?: string | null;
            /** Bid Quote Notional */
            bid_quote_notional?: string | null;
            /**
             * Bids
             * @default []
             */
            bids?: components["schemas"]["BookLevel"][];
            /**
             * Calculation Method
             * @default visible-resting-depth/base-and-unrounded-quote/v1
             */
            calculation_method?: string;
            /**
             * Collected At
             * Format: date-time
             */
            collected_at: string;
            /** Content Hash */
            content_hash: string;
            /**
             * Coverage Kind
             * @default depth_limited_snapshot
             * @constant
             */
            coverage_kind?: "depth_limited_snapshot";
            /** Cross Sequence */
            cross_sequence?: number | null;
            /** Event Time */
            event_time?: string | null;
            /**
             * Excluded Liquidity
             * @default RPI
             * @constant
             */
            excluded_liquidity?: "RPI";
            freshness?: components["schemas"]["FreshnessEvaluation-Output"] | null;
            /**
             * Freshness Policy Version
             * @default resting-book/event-age-10s/no-future/v1
             */
            freshness_policy_version?: string;
            /**
             * Historical Coverage
             * @default false
             * @constant
             */
            historical_coverage?: false;
            identity: components["schemas"]["EvidenceMarketIdentity-Output"];
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Price Units */
            price_units: string;
            /** Provider Generated At */
            provider_generated_at?: string | null;
            /** Quote Units */
            quote_units: string;
            /** Reason */
            reason?: string | null;
            /**
             * Requested Depth
             * @default 20
             * @constant
             */
            requested_depth?: 20;
            /** Resting Base Imbalance Ratio */
            resting_base_imbalance_ratio?: string | null;
            /**
             * Sequence Status
             * @default independent_snapshot
             * @enum {string}
             */
            sequence_status?: "independent_snapshot" | "resync_required";
            /** Spread */
            spread?: string | null;
            /** Update Id */
            update_id?: number | null;
        };
        /** OrderFlowObservation */
        "OrderFlowObservation-Output": {
            availability: components["schemas"]["EvidenceAvailability"];
            /** Base Units */
            base_units: string;
            /**
             * Baseline
             * @default 0
             */
            baseline?: string;
            /**
             * Calculation Method
             * @default real-aggressor-prints/base-and-unrounded-quote/5m/v1
             */
            calculation_method?: string;
            completeness: components["schemas"]["DataCompleteness"];
            /** Content Hash */
            content_hash: string;
            /** Coverage Content Hash */
            coverage_content_hash?: string | null;
            /**
             * Coverage Kind
             * @default unproven
             * @enum {string}
             */
            coverage_kind?: "proven_executed_trade_window" | "unproven";
            /** Cvd Change */
            cvd_change?: string | null;
            /** Cvd Divergence */
            cvd_divergence?: string | null;
            /** Cvd Slope Base Per Second */
            cvd_slope_base_per_second?: string | null;
            /** Cvd Supportive Side */
            cvd_supportive_side?: string | null;
            /** Cvd Units */
            cvd_units: string;
            /** Cvd Weakening */
            cvd_weakening?: boolean | null;
            /** Event Time */
            event_time: string | null;
            freshness: components["schemas"]["FreshnessEvaluation-Output"] | null;
            /** Freshness Policy Version */
            freshness_policy_version: string;
            identity: components["schemas"]["EvidenceMarketIdentity-Output"];
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Order Flow Strengthening Side */
            order_flow_strengthening_side?: string | null;
            /** Quote Units */
            quote_units: string;
            /** Reason */
            reason?: string | null;
            /**
             * Reset Semantics
             * @default zero-at-10m-window-start;rebuild-on-window-roll-or-venue-switch/v1
             */
            reset_semantics?: string;
            /** Rolling Cvd */
            rolling_cvd?: string | null;
            /** Rolling Quote Cvd */
            rolling_quote_cvd?: string | null;
            /** Series Identity */
            series_identity: string;
            /**
             * State Method
             * @default last-vs-prior-5m;directional-delta-and-quote-imbalance;print-close/v1
             */
            state_method?: string;
            /** Trade Set Hash */
            trade_set_hash?: string | null;
            /**
             * Window End
             * Format: date-time
             */
            window_end: string;
            /**
             * Window Start
             * Format: date-time
             */
            window_start: string;
            /**
             * Windows
             * @default []
             */
            windows?: components["schemas"]["FiveMinuteFlow-Output"][];
        };
        /** PaginatedRagChunks */
        PaginatedRagChunks: {
            /** Items */
            items: components["schemas"]["RagChunk"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** PaginatedRagDocuments */
        PaginatedRagDocuments: {
            /** Items */
            items: components["schemas"]["RagDocument"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** PaginatedUserStrategyVersions */
        PaginatedUserStrategyVersions: {
            /** Items */
            items: components["schemas"]["UserStrategyVersion"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** PaperAccountStatus */
        PaperAccountStatus: {
            account: components["schemas"]["PaperExecutionAccount"] | null;
            /** Can Register */
            can_register: boolean;
        };
        /** PaperExecutionAccount */
        PaperExecutionAccount: {
            account_mode: components["schemas"]["AccountMode"];
            /** Enabled */
            enabled: boolean;
            execution_mode: components["schemas"]["app__schemas__trade_plan__ExecutionMode"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
        };
        /** PaperPreTradeAnalysis */
        PaperPreTradeAnalysis: {
            /** Entry */
            entry: string;
            /** Risk Reward Ratios */
            risk_reward_ratios: string[];
            /** Stop */
            stop: string;
            /** Targets */
            targets: string[];
        };
        /** PaperSafetyContract */
        PaperSafetyContract: {
            /**
             * Agent Can Enable Real Trading
             * @default false
             * @constant
             */
            agent_can_enable_real_trading?: false;
            /** Exchange Mode */
            exchange_mode: string;
            /**
             * Execution Attempted
             * @default false
             * @constant
             */
            execution_attempted?: false;
            /**
             * Execution Mode
             * @default paper
             * @constant
             */
            execution_mode?: "paper";
            /**
             * Real Trading Enabled
             * @default false
             * @constant
             */
            real_trading_enabled?: false;
        };
        /**
         * PaperValidationStatus
         * @description Paper validation lifecycle.
         * @enum {string}
         */
        PaperValidationStatus: "not_started" | "in_progress" | "passed" | "failed";
        /**
         * PlanPresentationMetadata
         * @description Non-semantic display data accepted at the plan boundary.
         */
        PlanPresentationMetadata: {
            channel?: components["schemas"]["AuthorizationChannel"] | null;
            /** Decision Reference */
            decision_reference?: string | null;
            /** Display Title */
            display_title?: string | null;
            /** Evidence Reference */
            evidence_reference?: string | null;
            /** Notes */
            notes?: string | null;
            /** Setup Id */
            setup_id?: string | null;
            /** Strategy Id */
            strategy_id?: string | null;
            /** Strategy Version Id */
            strategy_version_id?: string | null;
        };
        /**
         * PrivacyClass
         * @enum {string}
         */
        PrivacyClass: "public_market_data";
        /**
         * ProductFamily
         * @enum {string}
         */
        ProductFamily: "usdm_futures" | "coinm_futures" | "spot";
        /** ProposalDecisionRequest */
        ProposalDecisionRequest: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /** Expected Content Hash */
            expected_content_hash: string;
            /** Statement */
            statement: string;
        };
        /**
         * ProposalLifecycle
         * @enum {string}
         */
        ProposalLifecycle: "proposed" | "confirmed_unapplied" | "applied" | "rejected" | "refused";
        /**
         * ProvenanceSource
         * @enum {string}
         */
        ProvenanceSource: "user_supplied" | "agent_inferred" | "watcher_observed" | "trade_outcome" | "system_generated";
        /**
         * ProviderProvenance
         * @description Read-only provider provenance attached to every evidence envelope.
         */
        ProviderProvenance: {
            /** Adapter Version */
            adapter_version: string;
            /** Detail */
            detail?: string | null;
            /** Fallback Used */
            fallback_used: boolean;
            /** Is Live */
            is_live: boolean;
            /** Is Mock */
            is_mock: boolean;
            /** Provider Name */
            provider_name: string;
            /**
             * Regional Failure
             * @default false
             */
            regional_failure?: boolean;
            source_family: components["schemas"]["SourceFamily"];
        };
        /**
         * PublicMarketObservation
         * @description Global public venue fact. No tenant owner.
         */
        "PublicMarketObservation-Output": {
            /** Content Hash */
            content_hash: string;
            /**
             * Event Time
             * Format: date-time
             */
            event_time: string;
            finality: components["schemas"]["Finality"];
            freshness_state: components["schemas"]["FreshnessState"];
            identity: components["schemas"]["EvidenceMarketIdentity-Output"];
            /** Interval End */
            interval_end?: string | null;
            /** Interval Start */
            interval_start?: string | null;
            /**
             * Observation Id
             * Format: uuid
             */
            observation_id: string;
            observation_type: components["schemas"]["ObservationType"];
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Payload Content Hash */
            payload_content_hash: string;
            /** @default public_market_data */
            privacy_class?: components["schemas"]["PrivacyClass"];
            /**
             * Receive Time
             * Format: date-time
             */
            receive_time: string;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /** Revision */
            revision: number;
            /** Source Event Id */
            source_event_id: string;
            /**
             * Source Time
             * Format: date-time
             */
            source_time: string;
        };
        /**
         * QuantityUnit
         * @enum {string}
         */
        QuantityUnit: "CONTRACTS" | "BASE" | "QUOTE";
        /**
         * RagChunk
         * @description A retrievable chunk of a document.
         */
        RagChunk: {
            /** Chunk Ordinal */
            chunk_ordinal: number;
            /** Content */
            content: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /**
             * Embedding Ref
             * @description Qdrant point id, if embedded.
             */
            embedding_ref?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            metadata: components["schemas"]["ChunkMetadata"];
            /** Organization Id */
            organization_id?: string | null;
            /** Page Number */
            page_number?: number | null;
            /** Section Title */
            section_title?: string | null;
            /** Text Hash */
            text_hash?: string | null;
            /** Title */
            title?: string | null;
            /** Token Count */
            token_count?: number | null;
            /** User Id */
            user_id?: string | null;
        };
        /**
         * RagDocument
         * @description A source document in the knowledge base.
         */
        RagDocument: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            ingestion_metadata?: components["schemas"]["DocumentIngestionMetadata"] | null;
            /** Organization Id */
            organization_id?: string | null;
            /** Source Hash */
            source_hash?: string | null;
            source_type: components["schemas"]["DocumentSourceType"];
            /** Source Uri */
            source_uri?: string | null;
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** User Id */
            user_id?: string | null;
            /**
             * Version
             * @default 1
             */
            version?: number;
        };
        /**
         * ReviewClass
         * @enum {string}
         */
        ReviewClass: "fact" | "user_observation" | "system_inference" | "research_suggestion";
        /** ReviewItem */
        ReviewItem: {
            /** Candidate Id */
            candidate_id?: string | null;
            classification: components["schemas"]["ReviewClass"];
            /** Code */
            code: string;
            /** Sources */
            sources: components["schemas"]["ReviewSource"][];
            /** Strategy Version Id */
            strategy_version_id?: string | null;
            /** Text */
            text?: string | null;
            topic: components["schemas"]["ReviewTopic"];
        };
        /** ReviewSource */
        ReviewSource: {
            /** Content Hash */
            content_hash?: string | null;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Record Id */
            record_id: string;
            /** Record Type */
            record_type: string;
            /** Upstream Event Id */
            upstream_event_id?: string | null;
            /** Upstream System */
            upstream_system?: string | null;
            /** Version */
            version?: number | null;
        };
        /**
         * ReviewTopic
         * @enum {string}
         */
        ReviewTopic: "watcher_activity" | "setups" | "paper_opened" | "paper_closed" | "blocked_candidates" | "risk_events" | "journal_entries" | "mistakes" | "lessons" | "missed_setups" | "strategy_observations" | "data_quality_limitations";
        /** ReviewWindow */
        ReviewWindow: {
            /**
             * Day
             * Format: date
             */
            day: string;
            /**
             * End
             * Format: date-time
             */
            end: string;
            /**
             * Start
             * Format: date-time
             */
            start: string;
            /** Timezone */
            timezone: string;
        };
        /**
         * RiskAction
         * @description Deterministic risk-engine verdict. Default-deny favors ``BLOCK``.
         * @enum {string}
         */
        RiskAction: "allow" | "warn" | "block";
        /** RiskAndExits */
        RiskAndExits: {
            fee_allowance: components["schemas"]["NonNegativeSemanticAmount"];
            funding_allowance: components["schemas"]["NonNegativeSemanticAmount"];
            /** Leverage */
            leverage: string;
            /** Margin Assumption Id */
            margin_assumption_id: string;
            /** Margin Assumption Version */
            margin_assumption_version: string;
            maximum_loss: components["schemas"]["SemanticAmount"];
            risk_budget: components["schemas"]["SemanticAmount"];
            runner: components["schemas"]["RunnerRules"];
            slippage_allowance: components["schemas"]["NonNegativeSemanticAmount"];
            stop: components["schemas"]["SemanticAmount"];
            /** Targets */
            targets: components["schemas"]["ExitTarget"][];
        };
        /**
         * RiskCheckResult
         * @description Aggregated, deterministic verdict.
         *
         *     The overall ``action`` is the most restrictive of all triggered rules; the
         *     engine defaults to ``BLOCK`` when inputs are insufficient to decide safely.
         */
        RiskCheckResult: {
            action: components["schemas"]["RiskAction"];
            /** Approval Required */
            approval_required: boolean;
            /** Explanation */
            explanation: string;
            severity: components["schemas"]["RiskSeverity"];
            /**
             * Suggested Modification
             * @description Optional safer parameters (e.g. reduced size/leverage).
             */
            suggested_modification?: {
                [key: string]: string;
            } | null;
            /** Triggered Rules */
            triggered_rules?: components["schemas"]["TriggeredRule"][];
        };
        /**
         * RiskRuleId
         * @description Stable identifiers for risk rules (reconciled with PRD/Architecture).
         * @enum {string}
         */
        RiskRuleId: "max_leverage" | "max_position_size" | "max_daily_loss" | "max_weekly_loss" | "no_stop_loss" | "invalid_stop_loss" | "unsupported_coin" | "countertrend_reduced_size" | "volatile_altcoin_reduced_size" | "extreme_funding" | "low_volume" | "weekend_condition" | "sleep_test" | "overtrading" | "strong_green_day" | "cooldown_after_loss" | "kill_switch";
        /**
         * RiskSeverity
         * @enum {string}
         */
        RiskSeverity: "info" | "low" | "medium" | "high" | "critical";
        /**
         * RuleCondition
         * @description Single condition within a rule block.
         */
        "RuleCondition-Output": {
            /**
             * Confirmation Required
             * @default false
             */
            confirmation_required?: boolean;
            /** Indicator */
            indicator?: string | null;
            /** Lookback Candles */
            lookback_candles?: number | null;
            operator?: components["schemas"]["RuleConditionOperator"] | null;
            timeframe?: components["schemas"]["Timeframe"] | null;
            /** Value */
            value?: string | null;
        };
        /**
         * RuleConditionOperator
         * @description Comparison operator for structured rule conditions.
         * @enum {string}
         */
        RuleConditionOperator: "gt" | "gte" | "lt" | "lte" | "eq" | "crosses_above" | "crosses_below";
        /** RunnerRules */
        RunnerRules: {
            /** Activation Target Order */
            activation_target_order?: number | null;
            /** Enabled */
            enabled: boolean;
            /** Expression */
            expression: string;
            /** Remaining Quantity Fraction */
            remaining_quantity_fraction: string;
            /** Rule Id */
            rule_id: string;
            /** Rule Version */
            rule_version: string;
        };
        /**
         * SampleConfidence
         * @description Coarse confidence label derived from closed-trade sample size.
         * @enum {string}
         */
        SampleConfidence: "insufficient" | "low" | "moderate" | "high";
        /** SavedEntriesPage */
        SavedEntriesPage: {
            /** Items */
            items: components["schemas"]["SavedEntry"][];
            /** Total */
            total: number;
        };
        /** SavedEntry */
        SavedEntry: {
            /**
             * Category
             * @enum {string}
             */
            category: "journal" | "rules" | "strategies" | "news_analysis" | "lessons";
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Draft */
            draft: {
                [key: string]: unknown;
            } | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Original Text */
            original_text: string;
            /** Revision */
            revision: number;
            /** Source Document Id */
            source_document_id: string | null;
            /** Source Message Ids */
            source_message_ids: string[];
            /** Summary */
            summary: string;
            /** Tags */
            tags: string[];
            /** Title */
            title: string;
            /** Trade Id */
            trade_id: string | null;
            /** Undone */
            undone: boolean;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ScreenshotAnalysisContract */
        ScreenshotAnalysisContract: {
            /**
             * Accepts Image
             * @default false
             * @constant
             */
            accepts_image?: false;
            /** Analysis */
            analysis?: null;
            /**
             * Analyzed
             * @default false
             * @constant
             */
            analyzed?: false;
            /**
             * Capability
             * @default screenshot_analysis
             * @constant
             */
            capability?: "screenshot_analysis";
            /**
             * Reason
             * @default Screenshot analysis is not implemented. No image was fetched or interpreted.
             */
            reason?: string;
            /**
             * Reference Received
             * @default false
             */
            reference_received?: boolean;
            /**
             * Status
             * @default contract_only
             * @constant
             */
            status?: "contract_only";
        };
        /** SemanticAmount */
        SemanticAmount: {
            /** Unit */
            unit: string;
            /** Value */
            value: string;
        };
        /** SlippagePolicy */
        SlippagePolicy: {
            /** Maximum Bps */
            maximum_bps: string;
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
        };
        /**
         * SourceFamily
         * @enum {string}
         */
        SourceFamily: "binance_usdm_futures_public" | "bybit_usdt_perpetual_public" | "replay_fixture";
        /** SourceIdentity */
        SourceIdentity: {
            /** Adapter Version */
            adapter_version: string;
            /** Aggressor Convention */
            aggressor_convention: string;
            family: components["schemas"]["SourceFamily"];
            /** Provider Name */
            provider_name: string;
        };
        /** StrategyAnalyticsBucket */
        StrategyAnalyticsBucket: {
            /** Dimensions */
            dimensions: {
                [key: string]: string | null;
            };
            metrics: components["schemas"]["StrategyAnalyticsMetrics"];
        };
        /**
         * StrategyAnalyticsDimension
         * @enum {string}
         */
        StrategyAnalyticsDimension: "strategy" | "strategy_version" | "symbol" | "timeframe" | "market_regime" | "nested_maturity_stage";
        /**
         * StrategyAnalyticsFilters
         * @description Dates use the journal's effective exit/entry/creation time, inclusively.
         *
         *     Strategy/version/stage filters apply after the bounded canonical scan, since
         *     attribution can come from a direct Strategy Brain journal link. Truncation
         *     therefore also means later matching trades may be absent from the sample.
         */
        StrategyAnalyticsFilters: {
            /** Date From */
            date_from?: string | null;
            /** Date To */
            date_to?: string | null;
            market_regime?: components["schemas"]["MarketRegime"] | null;
            nested_maturity_stage?: components["schemas"]["NestedMaturityStage"] | null;
            source?: components["schemas"]["JournalTradeSource"] | null;
            /** Strategy Id */
            strategy_id?: string | null;
            /** Strategy Version Id */
            strategy_version_id?: string | null;
            /** Symbol */
            symbol?: string | null;
            /** Timeframe */
            timeframe?: string | null;
        };
        /**
         * StrategyAnalyticsMetrics
         * @description Existing journal metrics plus coverage and missing foundation metrics.
         *
         *     Win rate retains journal semantics: wins / (wins + losses), excluding
         *     breakeven. Expectancy, R and profit factor use recorded net PnL. Costs are
         *     never deducted again. Drawdown is an absolute peak-to-trough decline of
         *     cumulative recorded net PnL, starting at zero, over trades with exit times;
         *     it is not account-equity drawdown. Holding period uses valid entry/exit
         *     pairs, even when PnL is missing. MAE/MFE are recorded monetary amounts.
         */
        StrategyAnalyticsMetrics: {
            /** Analytics Warnings */
            analytics_warnings?: components["schemas"]["StrategyAnalyticsWarning"][];
            /** Available Profit Total */
            available_profit_total?: string | null;
            /** Average Holding Period Seconds */
            average_holding_period_seconds?: number | null;
            /** Average Loser */
            average_loser?: string | null;
            /** Average Mae Amount */
            average_mae_amount?: string | null;
            /** Average Mfe Amount */
            average_mfe_amount?: string | null;
            /** Average R */
            average_r?: number | null;
            /** Average Realized Vs Available Pct */
            average_realized_vs_available_pct?: number | null;
            /** Average Winner */
            average_winner?: string | null;
            /**
             * Breakeven
             * @default 0
             */
            breakeven?: number;
            /**
             * Capture Sample Count
             * @default 0
             */
            capture_sample_count?: number;
            /** @default insufficient */
            confidence?: components["schemas"]["SampleConfidence"];
            /**
             * Cost Complete Sample Count
             * @default 0
             */
            cost_complete_sample_count?: number;
            /**
             * Cost Inconsistent Sample Count
             * @default 0
             */
            cost_inconsistent_sample_count?: number;
            /** Cost Reconciled Expectancy */
            cost_reconciled_expectancy?: string | null;
            /** Cost Reconciled Net Pnl Total */
            cost_reconciled_net_pnl_total?: string | null;
            /**
             * Cost Sample Count
             * @default 0
             */
            cost_sample_count?: number;
            /** Expectancy */
            expectancy?: string | null;
            /** Fees Total */
            fees_total?: string | null;
            /** Funding Total */
            funding_total?: string | null;
            /** Gross Pnl Total */
            gross_pnl_total?: string | null;
            /**
             * Insufficient History
             * @default true
             */
            insufficient_history?: boolean;
            /** Invalid Fields */
            invalid_fields?: {
                [key: string]: number;
            };
            /**
             * Losses
             * @default 0
             */
            losses?: number;
            /**
             * Mae Sample Count
             * @default 0
             */
            mae_sample_count?: number;
            /** Maximum Drawdown */
            maximum_drawdown?: string | null;
            /** Median R */
            median_r?: number | null;
            /** Metric Samples */
            metric_samples?: {
                [key: string]: components["schemas"]["StrategyAnalyticsSample"];
            };
            /**
             * Mfe Sample Count
             * @default 0
             */
            mfe_sample_count?: number;
            /** Missing Fields */
            missing_fields?: {
                [key: string]: number;
            };
            /** Net Pnl Total */
            net_pnl_total?: string | null;
            /**
             * Pnl Sample Count
             * @default 0
             */
            pnl_sample_count?: number;
            /** Profit Factor */
            profit_factor?: number | null;
            /**
             * R Sample Count
             * @default 0
             */
            r_sample_count?: number;
            /** Realized On Available Total */
            realized_on_available_total?: string | null;
            /** Slippage Total */
            slippage_total?: string | null;
            /** Total Costs */
            total_costs?: string | null;
            /**
             * Trade Count
             * @default 0
             */
            trade_count?: number;
            /** Warnings */
            warnings?: components["schemas"]["JournalStatsWarning"][];
            /** Win Rate */
            win_rate?: number | null;
            /**
             * Wins
             * @default 0
             */
            wins?: number;
        };
        /** StrategyAnalyticsReport */
        StrategyAnalyticsReport: {
            /** Buckets */
            buckets: components["schemas"]["StrategyAnalyticsBucket"][];
            /**
             * Contract Version
             * @default strategy-analytics/v1
             * @constant
             */
            contract_version?: "strategy-analytics/v1";
            filters: components["schemas"]["StrategyAnalyticsFilters"];
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Group By */
            group_by: components["schemas"]["StrategyAnalyticsDimension"][];
            /** Limit */
            limit: number;
            /**
             * Limitations
             * @default [
             *       "Descriptive recorded history only; sample thresholds do not establish a strategy edge.",
             *       "Positions and paper trades are not added again to their canonical journal outcomes.",
             *       "Unknown regime, blank dimensions and absent or ambiguous lineage remain unassigned.",
             *       "No missing costs, risk, excursions, timestamps or maturity stages are estimated.",
             *       "Cost reconciliation verifies recorded arithmetic, not execution-cost completeness.",
             *       "Drawdown covers only recorded net PnL with exit times; partial coverage is explicit.",
             *       "Strategy/version/stage filters run after the row cap; later matches may be omitted."
             *     ]
             */
            limitations?: string[];
            /** Max Rows */
            max_rows: number;
            /** Min Sample Size */
            min_sample_size: number;
            /** Offset */
            offset: number;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /**
             * Outcome Basis
             * @default closed_canonical_journal_trades
             * @constant
             */
            outcome_basis?: "closed_canonical_journal_trades";
            overall: components["schemas"]["StrategyAnalyticsMetrics"];
            /** Scanned Trade Count */
            scanned_trade_count: number;
            /**
             * Stage Basis
             * @default first_recorded_paper_trade_opened_event
             * @constant
             */
            stage_basis?: "first_recorded_paper_trade_opened_event";
            /** Total Buckets */
            total_buckets: number;
            /** Truncated */
            truncated: boolean;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * StrategyAnalyticsSample
         * @description The actual denominator for one metric; threshold is descriptive only.
         */
        StrategyAnalyticsSample: {
            /** Available */
            available: boolean;
            /** Insufficient History */
            insufficient_history: boolean;
            /** Sample Count */
            sample_count: number;
        };
        /** StrategyAnalyticsWarning */
        StrategyAnalyticsWarning: {
            /**
             * Code
             * @enum {string}
             */
            code: "missing_fields" | "invalid_fields" | "insufficient_history" | "costs_unverified" | "costs_inconsistent" | "ambiguous_brain_link" | "conflicting_brain_link";
            /** Message */
            message: string;
        };
        /**
         * StrategyBrainDefinition
         * @description Semantic metadata embedded in the existing immutable strategy card/version.
         */
        StrategyBrainDefinition: {
            /** Alert Rules */
            alert_rules?: string[];
            /** Confluence Inputs */
            confluence_inputs?: string[];
            /**
             * Created From
             * @default explicit user strategy proposal
             */
            created_from?: string;
            /** Evidence References */
            evidence_references?: string[];
            /** Execution Constraints */
            execution_constraints?: string[];
            /**
             * Family
             * @enum {string}
             */
            family: "nested_continuation" | "sfp" | "d_line" | "higher_timeframe_swing";
            /** Learning Notes */
            learning_notes?: string[];
            /**
             * Market Regime
             * @default directional continuation
             */
            market_regime?: string;
            /** Optional Inputs */
            optional_inputs?: string[];
            /** Required Inputs */
            required_inputs?: string[];
            /**
             * Risk Limits
             * @default existing_canonical_risk_limits
             * @constant
             */
            risk_limits?: "existing_canonical_risk_limits";
            /** Setup Conditions */
            setup_conditions?: string[];
            /** Statistics References */
            statistics_references?: string[];
            /** Structure Conditions */
            structure_conditions?: string[];
        };
        /**
         * StrategyCard
         * @description Structured strategy card per v5 brief.
         */
        "StrategyCard-Input": {
            /** Add Rules */
            add_rules?: string[];
            /** Asset Universe */
            asset_universe?: string[];
            /** Backtest Rules */
            backtest_rules?: string[];
            brain?: components["schemas"]["StrategyBrainDefinition"] | null;
            /** Confirmation Conditions */
            confirmation_conditions?: string[];
            /** Entry Conditions */
            entry_conditions?: string[];
            /** Invalidation */
            invalidation?: string[];
            /** @default crypto_perp */
            market_type?: components["schemas"]["app__schemas__common__MarketType"];
            /** No Trade Rules */
            no_trade_rules?: string[];
            /** Position Sizing */
            position_sizing?: string[];
            promotion_requirements?: components["schemas"]["StrategyPromotionRequirements"] | null;
            /** Runner Plan */
            runner_plan?: string[];
            /** Stop Loss */
            stop_loss?: string[];
            /** Strategy Name */
            strategy_name: string;
            /** Success Criteria */
            success_criteria?: string[];
            /** Take Profit Plan */
            take_profit_plan?: string[];
            /** Timeframes */
            timeframes?: components["schemas"]["Timeframe"][];
            /** @default draft */
            validation_status?: components["schemas"]["StrategyValidationStatus"];
        };
        "StrategyCard-Output": {
            [key: string]: unknown;
        };
        /** StrategyHit */
        StrategyHit: {
            /** Lifecycle Status */
            lifecycle_status?: string | null;
            /** Name */
            name: string;
            /** Paper Eligible */
            paper_eligible: boolean;
            /** @default user_supplied */
            provenance?: components["schemas"]["ProvenanceSource"];
            /** Selected Version Id */
            selected_version_id?: string | null;
            /** Setup Type */
            setup_type: string;
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /** Summary */
            summary: string;
            /** Validation Status */
            validation_status?: string | null;
            /** Version */
            version?: number | null;
        };
        /**
         * StrategyId
         * @description Trading setup types (strategy modules plus manual review).
         * @enum {string}
         */
        StrategyId: "htf_trend_pullback" | "liquidity_sweep_reversal" | "countertrend_short_build" | "passive_level_order" | "profit_protection" | "green_day_guard" | "mental_capital_guard" | "nested_continuation" | "sfp" | "manual_review";
        /**
         * StrategyPromotionRequirements
         * @description Authored requirements; absent values require explicit evidence review.
         */
        StrategyPromotionRequirements: {
            /** Minimum Paper Trades */
            minimum_paper_trades?: number | null;
            /** Minimum Replay Trades */
            minimum_replay_trades?: number | null;
        };
        /** StrategyProposalRecord */
        StrategyProposalRecord: {
            /** Challenge Notes */
            challenge_notes?: string[];
            /** Confirmation Request Id */
            confirmation_request_id?: string | null;
            /** Confirmed At */
            confirmed_at?: string | null;
            /** Content Hash */
            content_hash?: string | null;
            /** Context Refs */
            context_refs?: {
                [key: string]: unknown;
            };
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Is Preview
             * @default true
             */
            is_preview?: boolean;
            /** Limitations */
            limitations?: string[];
            /**
             * Mutates Strategy Authority
             * @default false
             */
            mutates_strategy_authority?: boolean;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Parent Version Id */
            parent_version_id?: string | null;
            /** Proposed Card */
            proposed_card?: {
                [key: string]: unknown;
            } | null;
            /** Proposed Pattern Spec */
            proposed_pattern_spec?: {
                [key: string]: unknown;
            } | null;
            proposed_structured_rules?: components["schemas"]["StructuredRules-Output"] | null;
            /** Rejected At */
            rejected_at?: string | null;
            /** Resulting Content Hash */
            resulting_content_hash?: string | null;
            /** Resulting Strategy Id */
            resulting_strategy_id?: string | null;
            /** Resulting Version Id */
            resulting_version_id?: string | null;
            /** Source Message Id */
            source_message_id?: string | null;
            status: components["schemas"]["StrategyProposalStatus"];
            /** Target Strategy Id */
            target_strategy_id?: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
            validation: components["schemas"]["StructuredRulesValidation"];
        };
        /**
         * StrategyProposalStatus
         * @description Structured strategy proposal preview. Drafts never mutate versions.
         * @enum {string}
         */
        StrategyProposalStatus: "draft" | "confirmed" | "rejected" | "superseded";
        /**
         * StrategyValidationStatus
         * @description Validation lifecycle for a user strategy card.
         * @enum {string}
         */
        StrategyValidationStatus: "draft" | "in_review" | "validated" | "restricted" | "retired" | "needs_revision" | "deprecated";
        /**
         * StructuredActionKind
         * @enum {string}
         */
        StructuredActionKind: "none" | "propose_observation" | "propose_hypothesis" | "propose_strategy" | "propose_rule" | "propose_journal_entry" | "propose_trade_decision" | "propose_lesson" | "propose_journal_append" | "propose_strategy_evidence" | "propose_validation_request" | "propose_knowledge" | "propose_watcher_change" | "enable_real_trading";
        /** StructuredActionProposal */
        StructuredActionProposal: {
            /** Application Result */
            application_result?: {
                [key: string]: unknown;
            };
            /**
             * Applied
             * @default false
             */
            applied?: boolean;
            artifact_kind: components["schemas"]["ArtifactKind"];
            /** Authority */
            authority: string;
            /**
             * Authority Mutated
             * @default false
             */
            authority_mutated?: boolean;
            /** Content Hash */
            content_hash: string;
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            kind: components["schemas"]["StructuredActionKind"];
            /** Linked Strategy Proposal Id */
            linked_strategy_proposal_id?: string | null;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /**
             * Proposal Id
             * Format: uuid
             */
            proposal_id: string;
            provenance: components["schemas"]["ProvenanceSource"];
            /** Resulting Record Id */
            resulting_record_id?: string | null;
            /**
             * Schema Version
             * @default InteractiveAgent/v1
             * @constant
             */
            schema_version?: "InteractiveAgent/v1";
            status: components["schemas"]["ProposalLifecycle"];
            /** Summary */
            summary: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * StructuredRules
         * @description Machine-testable rule bundle attached to a strategy version.
         */
        "StructuredRules-Output": {
            /** Entry Rules */
            entry_rules?: components["schemas"]["EntryRuleBlock-Output"][];
            /** Exit Rules */
            exit_rules?: components["schemas"]["ExitRuleBlock-Output"][];
            /** No Trade Rules */
            no_trade_rules?: components["schemas"]["NoTradeRuleBlock-Output"][];
            primary_timeframe?: components["schemas"]["Timeframe"] | null;
        };
        /**
         * StructuredRulesValidation
         * @description Validation result for structured rules.
         */
        StructuredRulesValidation: {
            /** Errors */
            errors?: string[];
            /** Valid */
            valid: boolean;
            /** Warnings */
            warnings?: string[];
        };
        /**
         * TimeInForce
         * @enum {string}
         */
        TimeInForce: "GTC" | "IOC" | "FOK" | "POST_ONLY";
        /**
         * Timeframe
         * @description Supported candle timeframes.
         * @enum {string}
         */
        Timeframe: "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "2h" | "4h" | "6h" | "12h" | "1d" | "3d" | "1w";
        /**
         * ToolOutput
         * @description Envelope for tool results, including success/error status.
         */
        ToolOutput: {
            /** Error */
            error?: string | null;
            /** Latency Ms */
            latency_ms?: number | null;
            /** Result */
            result?: {
                [key: string]: unknown;
            } | null;
            /** Success */
            success: boolean;
            /** Tool Name */
            tool_name: string;
            /**
             * Used Fallback
             * @default false
             */
            used_fallback?: boolean;
        };
        /**
         * TradeDirection
         * @enum {string}
         */
        TradeDirection: "long" | "short";
        /**
         * TradePlanRevision
         * @description Persisted immutable executable revision.
         */
        TradePlanRevision: {
            /**
             * Account Id
             * Format: uuid
             */
            account_id: string;
            basis_policy: components["schemas"]["BasisPolicy"];
            /** Calculation Inputs */
            calculation_inputs: components["schemas"]["CalculationInput"][];
            /** Candidate Id */
            candidate_id: string | null;
            /** Content Hash */
            content_hash: string;
            /**
             * Correlation Id
             * Format: uuid
             */
            correlation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            entry_zone: components["schemas"]["app__schemas__trade_plan__EntryZone"];
            entry_zone_derivation: components["schemas"]["VersionedDerivation"];
            /**
             * Evidence Fallback Used
             * @constant
             */
            evidence_fallback_used: false;
            /**
             * Evidence Final
             * @constant
             */
            evidence_final: true;
            /** Evidence Freshness Seconds */
            evidence_freshness_seconds: number;
            /** Evidence Ids */
            evidence_ids: string[];
            /** Evidence Instrument */
            evidence_instrument: string;
            /**
             * Evidence Is Live
             * @constant
             */
            evidence_is_live: true;
            evidence_market: components["schemas"]["app__schemas__trade_plan__MarketType"];
            /**
             * Evidence Observed At
             * Format: date-time
             */
            evidence_observed_at: string;
            /**
             * Evidence Sequence Complete
             * @constant
             */
            evidence_sequence_complete: true;
            /** Evidence Venue */
            evidence_venue: string;
            /** Exchange Account Id */
            exchange_account_id: string | null;
            /** Execution Instrument */
            execution_instrument: string;
            execution_market: components["schemas"]["app__schemas__trade_plan__MarketType"];
            /** Execution Policy Version */
            execution_policy_version: string;
            /** Execution Venue */
            execution_venue: string;
            /**
             * Expected Account Mode
             * @default NET
             * @constant
             */
            expected_account_mode?: "NET";
            /** Instrument Mapping Version */
            instrument_mapping_version: string;
            instrument_rules: components["schemas"]["InstrumentRules"];
            limit_price: components["schemas"]["SemanticAmount"] | null;
            margin_mode: components["schemas"]["MarginMode"];
            /** Market Marker */
            market_marker: boolean;
            /**
             * Operation
             * @default SUBMIT_ENTRY
             * @constant
             */
            operation?: "SUBMIT_ENTRY";
            order_type: components["schemas"]["EntryOrderType"];
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /**
             * Permission Attestation Id
             * Format: uuid
             */
            permission_attestation_id: string;
            /** Permission Attestation Version */
            permission_attestation_version: string;
            /**
             * Plan Id
             * Format: uuid
             */
            plan_id: string;
            /**
             * Position Mode
             * @default NET
             * @constant
             */
            position_mode?: "NET";
            presentation_metadata?: components["schemas"]["PlanPresentationMetadata"];
            quantity: components["schemas"]["SemanticAmount"];
            quantity_unit: components["schemas"]["QuantityUnit"];
            /**
             * Reduce Only
             * @default false
             * @constant
             */
            reduce_only?: false;
            /**
             * Revision Id
             * Format: uuid
             */
            revision_id: string;
            risk_and_exits: components["schemas"]["RiskAndExits"];
            /**
             * Schema Version
             * @default CanonicalTradePlanContentV1
             * @enum {string}
             */
            schema_version?: "CanonicalTradePlanContentV1" | "ManualDemoTradePlanV1";
            /** Setup Definition Id */
            setup_definition_id: string | null;
            side: components["schemas"]["EntrySide"];
            slippage_policy: components["schemas"]["SlippagePolicy"];
            /** Strategy Version Id */
            strategy_version_id: string | null;
            time_in_force: components["schemas"]["TimeInForce"];
            /** Timeframe */
            timeframe: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /**
             * Valid Until
             * Format: date-time
             */
            valid_until: string;
        };
        /**
         * TradingAnalysisDetail
         * @description Structured trading response — built deterministically, never raw LLM-only.
         */
        TradingAnalysisDetail: {
            /**
             * Approval Status
             * @description pending | not_required | blocked
             */
            approval_status: string;
            /** Confidence */
            confidence?: number | null;
            /** Evidence */
            evidence?: string[];
            /** Invalidation */
            invalidation?: string | null;
            /**
             * Market Data Quality
             * @description mock | stale | missing | live — transparency for market data source
             * @default mock
             */
            market_data_quality?: string;
            /** Next Decision Point */
            next_decision_point?: string | null;
            /** Paper Mode Disclaimer */
            paper_mode_disclaimer?: string | null;
            risk_level?: components["schemas"]["RiskSeverity"] | null;
            /** Setup Type */
            setup_type?: string | null;
            /** Stop Loss Or No Trade Reason */
            stop_loss_or_no_trade_reason: string;
            /** Summary */
            summary: string;
        };
        /**
         * TradingNarrativeDetail
         * @description Schema-validated LLM narrative polish. Extra fields forbidden.
         */
        TradingNarrativeDetail: {
            /** Caution Notes */
            caution_notes?: string[];
            /** Citations Used */
            citations_used?: string[];
            /** Evidence Explanation */
            evidence_explanation: string;
            /** Invalidation Explanation */
            invalidation_explanation: string;
            /** Limitations */
            limitations?: string[];
            /** Next Decision Point */
            next_decision_point: string;
            /** Paper Mode Disclaimer */
            paper_mode_disclaimer: string;
            /** Risk Explanation */
            risk_explanation: string;
            /** Setup Interpretation */
            setup_interpretation: string;
            /** Summary */
            summary: string;
        };
        /** TrendPulseEvidenceReference */
        TrendPulseEvidenceReference: {
            /**
             * Available At
             * Format: date-time
             */
            available_at: string;
            /** Observation Hash */
            observation_hash: string;
            /**
             * Observation Id
             * Format: uuid
             */
            observation_id: string;
            /** Payload Hash */
            payload_hash: string;
            timeframe: components["schemas"]["Timeframe"];
        };
        /** TrendPulseScreeningCreate */
        TrendPulseScreeningCreate: {
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Trigger End
             * Format: date-time
             */
            trigger_end: string;
            /** Variant Key */
            variant_key: string;
        };
        /** TrendPulseScreeningDetail */
        TrendPulseScreeningDetail: {
            /**
             * Acquisition Started At
             * Format: date-time
             */
            acquisition_started_at: string;
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Contract Version
             * @default trendpulse-screening/v1
             * @constant
             */
            contract_version?: "trendpulse-screening/v1";
            /**
             * Decision At
             * Format: date-time
             */
            decision_at: string;
            /** Duplicate Of */
            duplicate_of?: string | null;
            /** Entry Receipts */
            entry_receipts: number;
            evidence: components["schemas"]["TrendPulseScreeningEvidence"];
            /** Evidence Hash */
            evidence_hash: string;
            /**
             * Evidence Mode
             * @enum {string}
             */
            evidence_mode: "public_rest" | "replay";
            /**
             * Execution Authorized
             * @default false
             * @constant
             */
            execution_authorized?: false;
            /**
             * Experiment Id
             * Format: uuid
             */
            experiment_id: string;
            /**
             * Experiment Version Id
             * Format: uuid
             */
            experiment_version_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Management Authority
             * @default false
             * @constant
             */
            management_authority?: false;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Performance */
            performance?: null;
            /** Reason */
            reason: string;
            /**
             * Receipt Provenance
             * @enum {string}
             */
            receipt_provenance: "live_public_rest" | "recorded_public_receipts" | "synthetic_fixture";
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Sample Eligible
             * @default false
             * @constant
             */
            sample_eligible?: false;
            signal?: components["schemas"]["ExperimentBoundTrendPulse"] | null;
            /** Signal Id */
            signal_id?: string | null;
            status: components["schemas"]["TrendPulseStatus"];
            /** Strategy Content Hash */
            strategy_content_hash: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Trend Receipts */
            trend_receipts: number;
            /**
             * Trigger End
             * Format: date-time
             */
            trigger_end: string;
            /** Variant Key */
            variant_key: string;
        };
        /**
         * TrendPulseScreeningEvidence
         * @description Original canonical receipts, including partial acquisition on failure.
         */
        TrendPulseScreeningEvidence: {
            /**
             * Entry Bars
             * @default []
             */
            entry_bars?: components["schemas"]["OhlcvBar-Output"][];
            /**
             * Entry Observations
             * @default []
             */
            entry_observations?: components["schemas"]["PublicMarketObservation-Output"][];
            instrument_rules?: components["schemas"]["InstrumentRules"] | null;
            /**
             * Trend Bars
             * @default []
             */
            trend_bars?: components["schemas"]["OhlcvBar-Output"][];
            /**
             * Trend Observations
             * @default []
             */
            trend_observations?: components["schemas"]["PublicMarketObservation-Output"][];
        };
        /** TrendPulseScreeningPage */
        TrendPulseScreeningPage: {
            /** Items */
            items: components["schemas"]["TrendPulseScreeningRecord"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** TrendPulseScreeningRecord */
        TrendPulseScreeningRecord: {
            /**
             * Acquisition Started At
             * Format: date-time
             */
            acquisition_started_at: string;
            /** Configuration Hash */
            configuration_hash: string;
            /**
             * Contract Version
             * @default trendpulse-screening/v1
             * @constant
             */
            contract_version?: "trendpulse-screening/v1";
            /**
             * Decision At
             * Format: date-time
             */
            decision_at: string;
            /** Duplicate Of */
            duplicate_of?: string | null;
            /** Entry Receipts */
            entry_receipts: number;
            /** Evidence Hash */
            evidence_hash: string;
            /**
             * Evidence Mode
             * @enum {string}
             */
            evidence_mode: "public_rest" | "replay";
            /**
             * Execution Authorized
             * @default false
             * @constant
             */
            execution_authorized?: false;
            /**
             * Experiment Id
             * Format: uuid
             */
            experiment_id: string;
            /**
             * Experiment Version Id
             * Format: uuid
             */
            experiment_version_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Management Authority
             * @default false
             * @constant
             */
            management_authority?: false;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Performance */
            performance?: null;
            /** Reason */
            reason: string;
            /**
             * Receipt Provenance
             * @enum {string}
             */
            receipt_provenance: "live_public_rest" | "recorded_public_receipts" | "synthetic_fixture";
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Sample Eligible
             * @default false
             * @constant
             */
            sample_eligible?: false;
            /** Signal Id */
            signal_id?: string | null;
            status: components["schemas"]["TrendPulseStatus"];
            /** Strategy Content Hash */
            strategy_content_hash: string;
            /**
             * Strategy Version Id
             * Format: uuid
             */
            strategy_version_id: string;
            /** Trend Receipts */
            trend_receipts: number;
            /**
             * Trigger End
             * Format: date-time
             */
            trigger_end: string;
            /** Variant Key */
            variant_key: string;
        };
        /**
         * TrendPulseSignal
         * @description Closed-candle research geometry, never a Candidate or executable TradePlan.
         */
        TrendPulseSignal: {
            /**
             * Adapter Version
             * @default trendpulse-1r-research/v2
             * @constant
             */
            adapter_version?: "trendpulse-1r-research/v2";
            /** Content Hash */
            content_hash: string;
            /**
             * Decision At
             * Format: date-time
             */
            decision_at: string;
            direction: components["schemas"]["TradeDirection"];
            /** Ema50 Slope Ratio */
            ema50_slope_ratio: string;
            /** Entry */
            entry: string;
            /** Entry Ema20 */
            entry_ema20: string;
            /** Evidence */
            evidence: components["schemas"]["TrendPulseEvidenceReference"][];
            /**
             * Execution Authorized
             * @default false
             * @constant
             */
            execution_authorized?: false;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /**
             * Gross Reward Risk
             * @default 1
             * @constant
             */
            gross_reward_risk?: 1;
            identity: components["schemas"]["EvidenceMarketIdentity-Output"];
            /** Instrument Rules Hash */
            instrument_rules_hash: string;
            /**
             * Known At
             * Format: date-time
             */
            known_at: string;
            /**
             * Management Authority
             * @default false
             * @constant
             */
            management_authority?: false;
            /** Performance */
            performance?: null;
            /** Pullback Event Ids */
            pullback_event_ids: [
                string,
                string,
                string
            ];
            /**
             * Runtime Activated
             * @default false
             * @constant
             */
            runtime_activated?: false;
            /**
             * Signal Id
             * Format: uuid
             */
            signal_id: string;
            /** Spec Hash */
            spec_hash: string;
            /** Structural Extreme */
            structural_extreme: string;
            /** Structural Stop */
            structural_stop: string;
            /** Structure Anchor Ids */
            structure_anchor_ids: [
                string,
                string,
                string,
                string
            ];
            /** Target */
            target: string;
            /** Trend Ema20 */
            trend_ema20: string;
            /** Trend Ema50 */
            trend_ema50: string;
            /**
             * Trend End
             * Format: date-time
             */
            trend_end: string;
            /**
             * Trigger End
             * Format: date-time
             */
            trigger_end: string;
            /** Trigger Event Id */
            trigger_event_id: string;
        };
        /**
         * TrendPulseStatus
         * @enum {string}
         */
        TrendPulseStatus: "unavailable" | "refused" | "no_setup" | "qualified_research_signal" | "duplicate";
        /**
         * TriggeredRule
         * @description A single rule outcome contributing to the overall verdict.
         */
        TriggeredRule: {
            action: components["schemas"]["RiskAction"];
            /** Message */
            message: string;
            rule_id: components["schemas"]["RiskRuleId"];
            severity: components["schemas"]["RiskSeverity"];
        };
        /** TurnConflictDetails */
        TurnConflictDetails: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Reason
             * @enum {string}
             */
            reason: "turn_key_conflict" | "conversation_turn_in_progress" | "turn_running" | "turn_capture" | "turn_failed" | "turn_interrupted" | "turn_stale" | "turn_stale_snapshot";
            /**
             * Turn Id
             * Format: uuid
             */
            turn_id: string;
        };
        /** TurnConflictError */
        TurnConflictError: {
            /** Code */
            code: string;
            /** Details */
            details?: components["schemas"]["TurnConflictDetails"] | {
                [key: string]: unknown;
            } | null;
            /** Message */
            message: string;
            /** Request Id */
            request_id?: string | null;
        };
        /** TurnConflictResponse */
        TurnConflictResponse: {
            error: components["schemas"]["TurnConflictError"];
        };
        /**
         * TurnOperation
         * @enum {string}
         */
        TurnOperation: "read" | "propose" | "refuse";
        /**
         * UnavailableContextMetric
         * @description Capability limitation, not an observation or a zero-valued market fact.
         */
        UnavailableContextMetric: {
            /**
             * Availability
             * @default UNSUPPORTED
             * @constant
             */
            availability?: "UNSUPPORTED";
            /** Reason */
            reason: string;
            /** Value */
            value?: null;
        };
        /**
         * UsageEvent
         * @description A single metered LLM/tool interaction.
         */
        UsageEvent: {
            /**
             * Cache Hit
             * @default false
             */
            cache_hit?: boolean;
            /**
             * Cost Is Placeholder
             * @default true
             */
            cost_is_placeholder?: boolean;
            /** @default unavailable */
            cost_source?: components["schemas"]["CostSource"];
            /**
             * Estimated Cost
             * @default 0
             */
            estimated_cost?: string;
            /**
             * Fallback Used
             * @default false
             */
            fallback_used?: boolean;
            /** Feature */
            feature: string;
            /**
             * Input Tokens
             * @default 0
             */
            input_tokens?: number;
            /** Latency Ms */
            latency_ms?: number | null;
            /** Model */
            model?: string | null;
            /** Organization Id */
            organization_id?: string | null;
            /**
             * Output Tokens
             * @default 0
             */
            output_tokens?: number;
            /** Provider */
            provider?: string | null;
            /** Provider Reported Cost */
            provider_reported_cost?: string | null;
            /** Request Id */
            request_id?: string | null;
            /** @default success */
            status?: components["schemas"]["UsageStatus"];
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
            /**
             * Tool Calls
             * @default 0
             */
            tool_calls?: number;
            /**
             * Total Tokens
             * @default 0
             */
            total_tokens?: number;
            /** Usage Event Id */
            usage_event_id?: string | null;
            /** User Id */
            user_id?: string | null;
        };
        /**
         * UsageStatus
         * @enum {string}
         */
        UsageStatus: "success" | "failure" | "partial";
        /** UserStrategy */
        UserStrategy: {
            backtest_status?: components["schemas"]["BacktestStatus"] | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            latest_card?: components["schemas"]["StrategyCard-Output"] | null;
            /** Name */
            name: string;
            /** Notes */
            notes?: string | null;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /**
             * Paper Eligible
             * @default false
             */
            paper_eligible?: boolean;
            paper_validation_status?: components["schemas"]["PaperValidationStatus"] | null;
            setup_type: components["schemas"]["StrategyId"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
            validation_status?: components["schemas"]["StrategyValidationStatus"] | null;
        };
        /** UserStrategyUpdate */
        UserStrategyUpdate: {
            card?: components["schemas"]["StrategyCard-Input"] | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Name */
            name?: string | null;
            /** Notes */
            notes?: string | null;
            setup_type?: components["schemas"]["StrategyId"] | null;
        };
        /** UserStrategyVersion */
        UserStrategyVersion: {
            backtest_status: components["schemas"]["BacktestStatus"];
            card: components["schemas"]["StrategyCard-Output"];
            /** Content Hash */
            content_hash?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            lesson_source_metadata?: components["schemas"]["LessonSourceMetadata"] | null;
            paper_validation_status: components["schemas"]["PaperValidationStatus"];
            /** Pattern Spec */
            pattern_spec?: {
                [key: string]: unknown;
            } | null;
            /**
             * Strategy Id
             * Format: uuid
             */
            strategy_id: string;
            /** Structured Rules */
            structured_rules?: {
                [key: string]: unknown;
            } | null;
            validation_status: components["schemas"]["StrategyValidationStatus"];
            /** Version */
            version: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /**
         * VenueId
         * @enum {string}
         */
        VenueId: "binance" | "blofin" | "bybit";
        /** VersionedDerivation */
        VersionedDerivation: {
            /** Formula Id */
            formula_id: string;
            /** Formula Version */
            formula_version: string;
        };
        /** VoiceIoContract */
        VoiceIoContract: {
            /**
             * Audio Generated
             * @default false
             * @constant
             */
            audio_generated?: false;
            /**
             * Capability
             * @default voice_io
             * @constant
             */
            capability?: "voice_io";
            /**
             * Input Implemented
             * @default false
             * @constant
             */
            input_implemented?: false;
            /**
             * Output Implemented
             * @default false
             * @constant
             */
            output_implemented?: false;
            /**
             * Reason
             * @default Voice input and output are not implemented. No audio was transcribed or synthesized.
             */
            reason?: string;
            /**
             * Reference Received
             * @default false
             */
            reference_received?: boolean;
            /**
             * Status
             * @default contract_only
             * @constant
             */
            status?: "contract_only";
            /** Transcript */
            transcript?: null;
        };
        /**
         * MarketType
         * @enum {string}
         */
        app__market_contracts__enums__MarketType: "perpetual" | "spot" | "delivery" | "coin_m_perpetual" | "option";
        /**
         * MarketType
         * @description Market context for a user strategy card.
         * @enum {string}
         */
        app__schemas__common__MarketType: "crypto_perp" | "crypto_spot" | "forex" | "equities" | "commodities";
        /** EntryZone */
        app__schemas__trade_plan__EntryZone: {
            /** Lower */
            lower: string;
            /** Price Unit */
            price_unit: string;
            /** Upper */
            upper: string;
        };
        /**
         * ExecutionMode
         * @enum {string}
         */
        app__schemas__trade_plan__ExecutionMode: "PAPER";
        /**
         * MarketType
         * @enum {string}
         */
        app__schemas__trade_plan__MarketType: "PERPETUAL" | "SPOT";
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    agent_turn_agent_turns_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Stable UUID for turn recovery. */
                "idempotency-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentTurnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentTurnResult"];
                };
            };
            /** @description Durable turn recovery or domain conflict. Same-key recovery never repeats model I/O. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnConflictResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    send_message_chat_message_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Stable UUID for turn recovery. */
                "idempotency-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatMessageRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentMessageResponse"];
                };
            };
            /** @description Durable turn recovery or domain conflict. Same-key recovery never repeats model I/O. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnConflictResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_capture_agent_saved_retry_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Stable UUID for capture recovery. */
                "idempotency-key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureRetry"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SavedEntriesPage"];
                };
            };
            /** @description Durable turn recovery or domain conflict. Same-key recovery never repeats model I/O. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnConflictResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_documents_knowledge_documents_get: {
        parameters: {
            query?: {
                source_type?: components["schemas"]["DocumentSourceType"] | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaginatedRagDocuments"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_chunks_knowledge_chunks_get: {
        parameters: {
            query?: {
                document_id?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaginatedRagChunks"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ingest_document_knowledge_ingest_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IngestDocumentRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IngestDocumentResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_indexing_knowledge_documents__document_id__retry_indexing_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IngestDocumentResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_agent_proposal_agent_proposals__proposal_id__confirm_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                proposal_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProposalDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StructuredActionProposal"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reject_agent_proposal_agent_proposals__proposal_id__reject_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                proposal_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProposalDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StructuredActionProposal"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_strategy_strategies__strategy_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                strategy_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserStrategyUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserStrategy"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    attention_queue_dashboard_attention_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AttentionQueue"];
                };
            };
        };
    };
    daily_review_dashboard_daily_review_get: {
        parameters: {
            query?: {
                date?: string | null;
                timezone?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailyReview"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activity_exchange_blofin_activity_get: {
        parameters: {
            query?: {
                kind?: "order" | "fill";
                limit?: number;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listing_experiments_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_experiments_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detail_experiments__experiment_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fork_experiments__experiment_id__versions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentVersionCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    transition_experiments__experiment_id__versions__version_id__transition_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentTransition"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_experiments__experiment_id__versions__version_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentApproval"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    promote_experiments__experiment_id__versions__version_id__promote_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentPromotion"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentVersion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    native_outcome_experiments__experiment_id__versions__version_id__outcomes__source_record_id__get: {
        parameters: {
            query?: {
                variant_key?: string;
            };
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
                source_record_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NativeOutcome"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    native_performance_experiments__experiment_id__versions__version_id__performance_get: {
        parameters: {
            query?: {
                variant_key?: string;
            };
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentPerformance"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sample_experiments__experiment_id__versions__version_id__samples_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperimentSampleCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperimentSample"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listing_experiments__experiment_id__versions__version_id__trendpulse_screenings_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                status?: components["schemas"]["TrendPulseStatus"] | null;
            };
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrendPulseScreeningPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    screen_experiments__experiment_id__versions__version_id__trendpulse_screenings_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                experiment_id: string;
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrendPulseScreeningCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrendPulseScreeningDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    detail_trendpulse_screenings__record_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                record_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrendPulseScreeningDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_strategy_versions_strategies__strategy_id__versions_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                strategy_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaginatedUserStrategyVersions"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    paper_account_status_execution_accounts_paper_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaperAccountStatus"];
                };
            };
        };
    };
}
