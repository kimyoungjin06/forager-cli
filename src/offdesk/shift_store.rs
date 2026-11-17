//! Bounded, owner-only persistence for Offdesk shift authority and lifecycle.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{ErrorKind, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use super::{
    validate_offdesk_shift_state_transition, OffdeskShiftArmReceiptV1, OffdeskShiftManifestV1,
    OffdeskShiftRoutineOutcomeReceiptV1, OffdeskShiftRoutineRunReservationV1, OffdeskShiftStateV1,
    OffdeskShiftStatusV1, OffdeskShiftTaskLaunchReservationV1,
    OffdeskShiftTaskLaunchTicketReceiptV1, OffdeskShiftTaskOutcomeReceiptV1,
    OffdeskShiftTransitionReceiptV1,
};
use crate::process::FilesystemObjectSnapshot;

pub const OFFDESK_SHIFT_LEDGER_SCHEMA: &str = "offdesk_shift_ledger.v1";
pub const OFFDESK_SHIFT_LEDGER_FILE: &str = "offdesk_shifts.v1.json";
pub const OFFDESK_SHIFT_STORE_SNAPSHOT_SCHEMA: &str = "offdesk_shift_store_snapshot.v1";

const LOCK_FILE: &str = ".offdesk_shifts.v1.lock";
const MAX_LEDGER_BYTES: usize = 8 * 1024 * 1024;
const MAX_MANIFESTS: usize = 256;
const MAX_TRANSITIONS: usize = 4096;
const MAX_TASK_LAUNCH_RESERVATIONS: usize = 4096;
const MAX_TASK_LAUNCH_TICKET_RECEIPTS: usize = 4096;
const MAX_TASK_OUTCOME_RECEIPTS: usize = 4096;
const MAX_ROUTINE_RUN_RESERVATIONS: usize = 4096;
const MAX_ROUTINE_OUTCOME_RECEIPTS: usize = 4096;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct OffdeskShiftLedgerV1 {
    schema: String,
    manifests: Vec<OffdeskShiftManifestV1>,
    arm_receipts: Vec<OffdeskShiftArmReceiptV1>,
    states: Vec<OffdeskShiftStateV1>,
    transitions: Vec<OffdeskShiftTransitionReceiptV1>,
    #[serde(default)]
    task_launch_reservations: Vec<OffdeskShiftTaskLaunchReservationV1>,
    #[serde(default)]
    task_launch_ticket_receipts: Vec<OffdeskShiftTaskLaunchTicketReceiptV1>,
    #[serde(default)]
    task_outcome_receipts: Vec<OffdeskShiftTaskOutcomeReceiptV1>,
    #[serde(default)]
    routine_run_reservations: Vec<OffdeskShiftRoutineRunReservationV1>,
    #[serde(default)]
    routine_outcome_receipts: Vec<OffdeskShiftRoutineOutcomeReceiptV1>,
}

impl Default for OffdeskShiftLedgerV1 {
    fn default() -> Self {
        Self {
            schema: OFFDESK_SHIFT_LEDGER_SCHEMA.to_string(),
            manifests: Vec::new(),
            arm_receipts: Vec::new(),
            states: Vec::new(),
            transitions: Vec::new(),
            task_launch_reservations: Vec::new(),
            task_launch_ticket_receipts: Vec::new(),
            task_outcome_receipts: Vec::new(),
            routine_run_reservations: Vec::new(),
            routine_outcome_receipts: Vec::new(),
        }
    }
}

impl OffdeskShiftLedgerV1 {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_LEDGER_SCHEMA,
            "shift ledger schema must be {OFFDESK_SHIFT_LEDGER_SCHEMA}"
        );
        ensure!(
            self.manifests.len() <= MAX_MANIFESTS,
            "shift ledger exceeds its manifest budget"
        );
        ensure!(
            self.arm_receipts.len() <= self.manifests.len(),
            "shift ledger has more arm receipts than manifests"
        );
        ensure!(
            self.states.len() <= self.arm_receipts.len(),
            "shift ledger has more states than arm receipts"
        );
        ensure!(
            self.transitions.len() <= MAX_TRANSITIONS,
            "shift ledger exceeds its transition budget"
        );
        ensure!(
            self.task_launch_reservations.len() <= MAX_TASK_LAUNCH_RESERVATIONS,
            "shift ledger exceeds its task launch reservation budget"
        );
        ensure!(
            self.task_launch_ticket_receipts.len() <= MAX_TASK_LAUNCH_TICKET_RECEIPTS,
            "shift ledger exceeds its task launch ticket budget"
        );
        ensure!(
            self.task_outcome_receipts.len() <= MAX_TASK_OUTCOME_RECEIPTS,
            "shift ledger exceeds its task outcome budget"
        );
        ensure!(
            self.routine_run_reservations.len() <= MAX_ROUTINE_RUN_RESERVATIONS,
            "shift ledger exceeds its routine reservation budget"
        );
        ensure!(
            self.routine_outcome_receipts.len() <= MAX_ROUTINE_OUTCOME_RECEIPTS,
            "shift ledger exceeds its routine outcome budget"
        );

        let mut manifest_by_shift = HashMap::new();
        for manifest in &self.manifests {
            manifest.validate()?;
            ensure!(
                manifest_by_shift
                    .insert(manifest.shift_id.as_str(), manifest)
                    .is_none(),
                "shift ledger manifest IDs must be unique"
            );
        }

        let mut receipt_ids = HashSet::new();
        let mut receipt_by_shift = HashMap::new();
        for receipt in &self.arm_receipts {
            ensure!(
                receipt_ids.insert(receipt.receipt_id.as_str()),
                "shift arm receipt IDs must be unique"
            );
            let manifest = manifest_by_shift
                .get(receipt.shift_id.as_str())
                .context("shift arm receipt has no manifest")?;
            receipt.validate_against_manifest(manifest)?;
            ensure!(
                receipt_by_shift
                    .insert(receipt.shift_id.as_str(), receipt)
                    .is_none(),
                "shift ledger allows only one arm receipt per shift"
            );
        }

        let mut state_by_shift = HashMap::new();
        let mut active_shift_count = 0usize;
        for state in &self.states {
            let manifest = manifest_by_shift
                .get(state.shift_id.as_str())
                .context("shift state has no manifest")?;
            let receipt = receipt_by_shift
                .get(state.shift_id.as_str())
                .context("shift state has no arm receipt")?;
            state.validate_against(manifest, receipt)?;
            ensure!(
                state_by_shift
                    .insert(state.shift_id.as_str(), state)
                    .is_none(),
                "shift ledger state IDs must be unique"
            );
            if !state.status.is_terminal() {
                active_shift_count += 1;
            }
        }
        ensure!(
            active_shift_count <= 1,
            "shift ledger cannot contain multiple active shifts"
        );
        ensure!(
            receipt_by_shift.len() == state_by_shift.len(),
            "every shift arm receipt must have one current state"
        );

        let mut transition_ids = HashSet::new();
        let mut replay_by_shift: HashMap<&str, OffdeskShiftStateV1> = HashMap::new();
        for transition in &self.transitions {
            ensure!(
                transition_ids.insert(transition.transition_id.as_str()),
                "shift transition IDs must be unique"
            );
            let manifest = manifest_by_shift
                .get(transition.shift_id.as_str())
                .context("shift transition has no manifest")?;
            let receipt = receipt_by_shift
                .get(transition.shift_id.as_str())
                .context("shift transition has no arm receipt")?;
            match replay_by_shift.get(transition.shift_id.as_str()) {
                Some(previous) => transition.validate_after(previous, manifest, receipt)?,
                None => transition.validate_initial(manifest, receipt)?,
            }
            replay_by_shift.insert(transition.shift_id.as_str(), transition.state());
        }

        ensure!(
            replay_by_shift.len() == state_by_shift.len(),
            "every current shift state must have transition history"
        );
        for (shift_id, state) in &state_by_shift {
            ensure!(
                replay_by_shift
                    .get(shift_id)
                    .is_some_and(|replay| replay == *state),
                "current shift state does not equal transition replay"
            );
        }

        let mut launch_ids = HashSet::new();
        let mut attempts = HashSet::new();
        let mut launched_tasks_by_shift: HashMap<&str, HashSet<&str>> = HashMap::new();
        for reservation in &self.task_launch_reservations {
            ensure!(
                launch_ids.insert(reservation.launch_id.as_str()),
                "shift task launch reservation IDs must be unique"
            );
            ensure!(
                attempts.insert((
                    reservation.shift_id.as_str(),
                    reservation.task_id.as_str(),
                    reservation.attempt_number,
                )),
                "shift task launch attempts must be unique"
            );
            let manifest = manifest_by_shift
                .get(reservation.shift_id.as_str())
                .context("shift task launch reservation has no manifest")?;
            let receipt = receipt_by_shift
                .get(reservation.shift_id.as_str())
                .context("shift task launch reservation has no arm receipt")?;
            reservation.validate_against(manifest, receipt)?;
            launched_tasks_by_shift
                .entry(reservation.shift_id.as_str())
                .or_default()
                .insert(reservation.task_id.as_str());
        }
        for (shift_id, task_ids) in launched_tasks_by_shift {
            let manifest = manifest_by_shift
                .get(shift_id)
                .context("shift launch budget has no manifest")?;
            ensure!(
                task_ids.len() <= usize::from(manifest.budget.max_task_launches),
                "shift task launch reservations exceed the unique task budget"
            );
        }

        let reservation_by_launch = self
            .task_launch_reservations
            .iter()
            .map(|reservation| (reservation.launch_id.as_str(), reservation))
            .collect::<HashMap<_, _>>();
        let mut ticket_receipt_ids = HashSet::new();
        let mut ticket_by_launch = HashMap::new();
        for ticket in &self.task_launch_ticket_receipts {
            ensure!(
                ticket_receipt_ids.insert(ticket.receipt_id.as_str()),
                "shift task ticket receipt IDs must be unique"
            );
            let reservation = reservation_by_launch
                .get(ticket.launch_id.as_str())
                .context("shift task ticket receipt has no reservation")?;
            let manifest = manifest_by_shift
                .get(ticket.shift_id.as_str())
                .context("shift task ticket receipt has no manifest")?;
            let arm_receipt = receipt_by_shift
                .get(ticket.shift_id.as_str())
                .context("shift task ticket receipt has no arm receipt")?;
            ticket.validate_against(reservation, manifest, arm_receipt)?;
            ensure!(
                ticket_by_launch
                    .insert(ticket.launch_id.as_str(), ticket)
                    .is_none(),
                "shift task launches may have only one ticket receipt"
            );
        }

        let mut task_outcome_ids = HashSet::new();
        let mut task_outcome_launches = HashSet::new();
        for outcome in &self.task_outcome_receipts {
            ensure!(
                task_outcome_ids.insert(outcome.receipt_id.as_str()),
                "shift task outcome receipt IDs must be unique"
            );
            ensure!(
                task_outcome_launches.insert(outcome.launch_id.as_str()),
                "shift task launches may have only one terminal outcome"
            );
            let reservation = reservation_by_launch
                .get(outcome.launch_id.as_str())
                .context("shift task outcome has no reservation")?;
            let manifest = manifest_by_shift
                .get(outcome.shift_id.as_str())
                .context("shift task outcome has no manifest")?;
            let arm_receipt = receipt_by_shift
                .get(outcome.shift_id.as_str())
                .context("shift task outcome has no arm receipt")?;
            outcome.validate_against(
                reservation,
                ticket_by_launch.get(outcome.launch_id.as_str()).copied(),
                manifest,
                arm_receipt,
            )?;
        }

        let mut routine_run_ids = HashSet::new();
        let mut routine_occurrences = HashSet::new();
        let mut routine_runs_by_shift: HashMap<&str, usize> = HashMap::new();
        for reservation in &self.routine_run_reservations {
            ensure!(
                routine_run_ids.insert(reservation.run_id.as_str()),
                "shift routine reservation IDs must be unique"
            );
            ensure!(
                routine_occurrences.insert((
                    reservation.shift_id.as_str(),
                    reservation.routine_id.as_str(),
                    reservation.scheduled_at,
                )),
                "shift routine scheduled occurrences must be unique"
            );
            let manifest = manifest_by_shift
                .get(reservation.shift_id.as_str())
                .context("shift routine reservation has no manifest")?;
            let receipt = receipt_by_shift
                .get(reservation.shift_id.as_str())
                .context("shift routine reservation has no arm receipt")?;
            reservation.validate_against(manifest, receipt)?;
            *routine_runs_by_shift
                .entry(reservation.shift_id.as_str())
                .or_default() += 1;
        }
        for (shift_id, run_count) in routine_runs_by_shift {
            let manifest = manifest_by_shift
                .get(shift_id)
                .context("shift routine budget has no manifest")?;
            ensure!(
                run_count <= usize::from(manifest.budget.max_routine_runs),
                "shift routine reservations exceed the manifest budget"
            );
        }

        let reservation_by_run = self
            .routine_run_reservations
            .iter()
            .map(|reservation| (reservation.run_id.as_str(), reservation))
            .collect::<HashMap<_, _>>();
        let mut outcome_ids = HashSet::new();
        let mut outcome_run_ids = HashSet::new();
        let mut routine_usage_by_shift: HashMap<&str, [u128; 5]> = HashMap::new();
        for outcome in &self.routine_outcome_receipts {
            ensure!(
                outcome_ids.insert(outcome.receipt_id.as_str()),
                "shift routine outcome receipt IDs must be unique"
            );
            ensure!(
                outcome_run_ids.insert(outcome.run_id.as_str()),
                "shift routine runs may have only one outcome"
            );
            let reservation = reservation_by_run
                .get(outcome.run_id.as_str())
                .context("shift routine outcome has no reservation")?;
            let manifest = manifest_by_shift
                .get(outcome.shift_id.as_str())
                .context("shift routine outcome has no manifest")?;
            let receipt = receipt_by_shift
                .get(outcome.shift_id.as_str())
                .context("shift routine outcome has no arm receipt")?;
            outcome.validate_against(reservation, manifest, receipt)?;
            if let Some(usage) = &outcome.usage {
                let totals = routine_usage_by_shift
                    .entry(outcome.shift_id.as_str())
                    .or_default();
                for (total, value) in totals.iter_mut().zip([
                    usage.process_starts,
                    usage.external_calls,
                    usage.disk_bytes_written,
                    usage.llm_tokens,
                    usage.cost_micro_usd,
                ]) {
                    *total = total
                        .checked_add(u128::from(value))
                        .context("shift routine cumulative usage overflow")?;
                }
            }
        }
        for (shift_id, totals) in routine_usage_by_shift {
            let manifest = manifest_by_shift
                .get(shift_id)
                .context("shift routine usage has no manifest")?;
            let budget = manifest
                .routine_resource_budget
                .as_ref()
                .context("shift routine usage has no manifest resource budget")?;
            ensure!(
                totals[0] <= u128::from(budget.max_process_starts)
                    && totals[1] <= u128::from(budget.max_external_calls)
                    && totals[2] <= u128::from(budget.max_disk_bytes_written)
                    && totals[3] <= u128::from(budget.max_llm_tokens)
                    && totals[4] <= u128::from(budget.max_cost_micro_usd),
                "shift routine cumulative usage exceeds its manifest resource budget"
            );
        }
        let mut unresolved_by_shift: HashMap<&str, usize> = HashMap::new();
        for reservation in &self.routine_run_reservations {
            if !outcome_run_ids.contains(reservation.run_id.as_str()) {
                *unresolved_by_shift
                    .entry(reservation.shift_id.as_str())
                    .or_default() += 1;
            }
        }
        ensure!(
            unresolved_by_shift.values().all(|count| *count <= 1),
            "shift ledger allows only one unresolved routine run per shift"
        );
        Ok(())
    }

    fn validate_for_profile(&self, profile: &str) -> Result<()> {
        self.validate()?;
        ensure!(
            self.manifests
                .iter()
                .all(|manifest| manifest.profile == profile),
            "shift ledger contains a manifest for a different profile"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRecordV1 {
    pub manifest: OffdeskShiftManifestV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub arm_receipt: Option<OffdeskShiftArmReceiptV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<OffdeskShiftStateV1>,
    pub transitions: Vec<OffdeskShiftTransitionReceiptV1>,
    pub task_launch_reservations: Vec<OffdeskShiftTaskLaunchReservationV1>,
    pub task_launch_ticket_receipts: Vec<OffdeskShiftTaskLaunchTicketReceiptV1>,
    pub task_outcome_receipts: Vec<OffdeskShiftTaskOutcomeReceiptV1>,
    pub routine_run_reservations: Vec<OffdeskShiftRoutineRunReservationV1>,
    pub routine_outcome_receipts: Vec<OffdeskShiftRoutineOutcomeReceiptV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftStoreSnapshotV1 {
    pub schema: String,
    pub records: Vec<OffdeskShiftRecordV1>,
}

impl OffdeskShiftStoreSnapshotV1 {
    pub fn latest(&self) -> Option<&OffdeskShiftRecordV1> {
        self.records.last()
    }

    pub fn active(&self) -> Option<&OffdeskShiftRecordV1> {
        self.records.iter().find(|record| {
            record
                .state
                .as_ref()
                .is_some_and(|state| !state.status.is_terminal())
        })
    }
}

#[derive(Debug, Clone)]
pub struct OffdeskShiftStore {
    profile_root: PathBuf,
    profile: String,
}

impl OffdeskShiftStore {
    pub(crate) fn read_only_at_root(profile_root: &Path) -> Result<Self> {
        ensure!(
            profile_root.is_absolute(),
            "shift profile root must be absolute"
        );
        let name = profile_root
            .file_name()
            .and_then(|name| name.to_str())
            .context("shift profile name unavailable")?;
        let profile = crate::session::normalize_profile_name(name)?;
        ensure!(profile == name, "shift profile name is not normalized");
        Ok(Self {
            profile_root: profile_root.to_path_buf(),
            profile,
        })
    }

    pub fn read_only(profile: &str) -> Result<Self> {
        let profile = crate::session::normalize_profile_name(profile)?;
        Ok(Self {
            profile_root: crate::session::profile_dir_path(&profile)?,
            profile,
        })
    }

    pub fn writable(profile: &str) -> Result<Self> {
        let profile = crate::session::normalize_profile_name(profile)?;
        Ok(Self {
            profile_root: crate::session::get_profile_dir(&profile)?,
            profile,
        })
    }

    #[cfg(test)]
    fn for_root(profile_root: PathBuf) -> Self {
        Self {
            profile_root,
            profile: crate::session::DEFAULT_PROFILE.to_string(),
        }
    }

    pub fn snapshot(&self) -> Result<OffdeskShiftStoreSnapshotV1> {
        let ledger = self.load_read_only()?;
        let mut records = ledger
            .manifests
            .iter()
            .map(|manifest| OffdeskShiftRecordV1 {
                manifest: manifest.clone(),
                arm_receipt: ledger
                    .arm_receipts
                    .iter()
                    .find(|receipt| receipt.shift_id == manifest.shift_id)
                    .cloned(),
                state: ledger
                    .states
                    .iter()
                    .find(|state| state.shift_id == manifest.shift_id)
                    .cloned(),
                transitions: ledger
                    .transitions
                    .iter()
                    .filter(|transition| transition.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
                task_launch_reservations: ledger
                    .task_launch_reservations
                    .iter()
                    .filter(|reservation| reservation.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
                task_launch_ticket_receipts: ledger
                    .task_launch_ticket_receipts
                    .iter()
                    .filter(|receipt| receipt.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
                task_outcome_receipts: ledger
                    .task_outcome_receipts
                    .iter()
                    .filter(|receipt| receipt.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
                routine_run_reservations: ledger
                    .routine_run_reservations
                    .iter()
                    .filter(|reservation| reservation.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
                routine_outcome_receipts: ledger
                    .routine_outcome_receipts
                    .iter()
                    .filter(|receipt| receipt.shift_id == manifest.shift_id)
                    .cloned()
                    .collect(),
            })
            .collect::<Vec<_>>();
        records.sort_by(|left, right| {
            left.manifest
                .created_at
                .cmp(&right.manifest.created_at)
                .then_with(|| left.manifest.shift_id.cmp(&right.manifest.shift_id))
        });
        Ok(OffdeskShiftStoreSnapshotV1 {
            schema: OFFDESK_SHIFT_STORE_SNAPSHOT_SCHEMA.to_string(),
            records,
        })
    }

    pub fn record(&self, shift_id: &str) -> Result<Option<OffdeskShiftRecordV1>> {
        ensure_safe_shift_selector(shift_id)?;
        Ok(self
            .snapshot()?
            .records
            .into_iter()
            .find(|record| record.manifest.shift_id == shift_id))
    }

    pub fn save_manifest(&self, manifest: OffdeskShiftManifestV1) -> Result<bool> {
        manifest.validate()?;
        ensure!(
            manifest.profile == self.profile,
            "shift manifest profile does not match its store"
        );
        self.mutate(|ledger| {
            if let Some(existing) = ledger
                .manifests
                .iter()
                .find(|existing| existing.shift_id == manifest.shift_id)
            {
                let mut comparable = manifest.clone();
                comparable.created_at = existing.created_at;
                ensure!(
                    existing == &comparable,
                    "shift manifest ID already exists with different content"
                );
                return Ok(false);
            }
            ensure!(
                ledger.manifests.len() < MAX_MANIFESTS,
                "shift ledger manifest budget is exhausted"
            );
            ledger.manifests.push(manifest);
            Ok(true)
        })
    }

    pub fn arm_shift(
        &self,
        arm_receipt: OffdeskShiftArmReceiptV1,
        initial_state: OffdeskShiftStateV1,
        transition_id: &str,
    ) -> Result<bool> {
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == arm_receipt.shift_id)
                .context("cannot arm a shift without its stored manifest")?;
            arm_receipt.validate_against_manifest(manifest)?;
            if let Some(existing) = ledger
                .arm_receipts
                .iter()
                .find(|existing| existing.shift_id == arm_receipt.shift_id)
            {
                ensure!(
                    existing == &arm_receipt,
                    "shift is already armed by a different approval receipt"
                );
                let expected = OffdeskShiftTransitionReceiptV1::from_state_transition(
                    transition_id,
                    None,
                    &initial_state,
                )?;
                ensure!(
                    ledger
                        .transitions
                        .iter()
                        .any(|transition| transition == &expected),
                    "shift arm history does not contain the exact initial transition"
                );
                return Ok(false);
            }
            ensure!(
                !ledger
                    .states
                    .iter()
                    .any(|state| state.shift_id == arm_receipt.shift_id),
                "shift has state without an arm receipt"
            );
            ensure!(
                initial_state.status == OffdeskShiftStatusV1::Armed
                    && initial_state.sequence == 1
                    && initial_state.updated_at == arm_receipt.approved_at,
                "initial shift state must be sequence 1 at arm approval time"
            );
            initial_state.validate_against(manifest, &arm_receipt)?;
            let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                transition_id,
                None,
                &initial_state,
            )?;
            transition.validate_initial(manifest, &arm_receipt)?;
            ledger.arm_receipts.push(arm_receipt);
            ledger.states.push(initial_state);
            ledger.transitions.push(transition);
            Ok(true)
        })
    }

    pub(crate) fn reserve_task_launch(
        &self,
        reservation: OffdeskShiftTaskLaunchReservationV1,
        running_state: Option<OffdeskShiftStateV1>,
        transition_id: &str,
    ) -> Result<bool> {
        ensure_safe_shift_selector(&reservation.launch_id)?;
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == reservation.shift_id)
                .context("shift task launch reservation has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == reservation.shift_id)
                .context("shift task launch reservation has no stored arm receipt")?;
            reservation.validate_against(manifest, arm_receipt)?;
            if let Some(existing) = ledger
                .task_launch_reservations
                .iter()
                .find(|existing| existing.launch_id == reservation.launch_id)
            {
                ensure!(
                    existing == &reservation,
                    "shift task launch reservation ID has different content"
                );
                return Ok(false);
            }
            let state_index = ledger
                .states
                .iter()
                .position(|state| state.shift_id == reservation.shift_id)
                .context("shift task launch reservation has no current state")?;
            let previous = ledger.states[state_index].clone();
            match previous.status {
                OffdeskShiftStatusV1::Armed => {
                    let next = running_state
                        .context("first shift task reservation requires Running state")?;
                    validate_offdesk_shift_state_transition(
                        &previous,
                        &next,
                        manifest,
                        arm_receipt,
                    )?;
                    let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                        transition_id,
                        Some(&previous),
                        &next,
                    )?;
                    transition.validate_after(&previous, manifest, arm_receipt)?;
                    ledger.states[state_index] = next;
                    ledger.transitions.push(transition);
                }
                OffdeskShiftStatusV1::Running => ensure!(
                    running_state.is_none(),
                    "Running shift must not repeat its initial transition"
                ),
                _ => anyhow::bail!("shift state does not permit a task launch reservation"),
            }
            ledger.task_launch_reservations.push(reservation);
            Ok(true)
        })
    }

    pub(crate) fn record_task_launch_ticket(
        &self,
        ticket: OffdeskShiftTaskLaunchTicketReceiptV1,
    ) -> Result<bool> {
        ensure_safe_shift_selector(&ticket.receipt_id)?;
        self.mutate(|ledger| {
            let reservation = ledger
                .task_launch_reservations
                .iter()
                .find(|reservation| reservation.launch_id == ticket.launch_id)
                .context("shift task ticket has no stored reservation")?;
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == ticket.shift_id)
                .context("shift task ticket has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == ticket.shift_id)
                .context("shift task ticket has no stored arm receipt")?;
            ticket.validate_against(reservation, manifest, arm_receipt)?;
            if let Some(existing) = ledger
                .task_launch_ticket_receipts
                .iter()
                .find(|existing| existing.launch_id == ticket.launch_id)
            {
                ensure!(
                    existing == &ticket,
                    "shift task ticket receipt already exists with different content"
                );
                return Ok(false);
            }
            ensure!(
                !ledger
                    .task_outcome_receipts
                    .iter()
                    .any(|outcome| outcome.launch_id == ticket.launch_id),
                "terminal shift task outcome already closed this launch"
            );
            ledger.task_launch_ticket_receipts.push(ticket);
            Ok(true)
        })
    }

    pub(crate) fn complete_task_launch(
        &self,
        outcome: OffdeskShiftTaskOutcomeReceiptV1,
        attention_state: Option<OffdeskShiftStateV1>,
        transition_id: &str,
    ) -> Result<bool> {
        ensure_safe_shift_selector(&outcome.receipt_id)?;
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let reservation = ledger
                .task_launch_reservations
                .iter()
                .find(|reservation| reservation.launch_id == outcome.launch_id)
                .context("shift task outcome has no stored reservation")?;
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == outcome.shift_id)
                .context("shift task outcome has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == outcome.shift_id)
                .context("shift task outcome has no stored arm receipt")?;
            let ticket = ledger
                .task_launch_ticket_receipts
                .iter()
                .find(|ticket| ticket.launch_id == outcome.launch_id);
            outcome.validate_against(reservation, ticket, manifest, arm_receipt)?;
            if let Some(existing) = ledger
                .task_outcome_receipts
                .iter()
                .find(|existing| existing.launch_id == outcome.launch_id)
            {
                ensure!(
                    existing == &outcome,
                    "shift task outcome already exists with different content"
                );
                return Ok(false);
            }
            ensure!(
                outcome.outcome.requires_attention() || attention_state.is_none(),
                "successful shift task outcome must not carry an attention transition"
            );
            if let Some(next) = attention_state {
                let state_index = ledger
                    .states
                    .iter()
                    .position(|state| state.shift_id == outcome.shift_id)
                    .context("shift task outcome has no current state")?;
                let previous = ledger.states[state_index].clone();
                ensure!(
                    previous.status == OffdeskShiftStatusV1::Running,
                    "shift task failure requires a Running shift"
                );
                validate_offdesk_shift_state_transition(&previous, &next, manifest, arm_receipt)?;
                let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                    transition_id,
                    Some(&previous),
                    &next,
                )?;
                transition.validate_after(&previous, manifest, arm_receipt)?;
                ledger.states[state_index] = next;
                ledger.transitions.push(transition);
            } else if outcome.outcome.requires_attention() {
                let current = ledger
                    .states
                    .iter()
                    .find(|state| state.shift_id == outcome.shift_id)
                    .context("shift task outcome has no current state")?;
                ensure!(
                    current.status != OffdeskShiftStatusV1::Running,
                    "failed shift task outcome must make a Running shift require attention"
                );
            }
            ledger.task_outcome_receipts.push(outcome);
            Ok(true)
        })
    }

    pub(crate) fn reserve_routine_run(
        &self,
        reservation: OffdeskShiftRoutineRunReservationV1,
        running_state: Option<OffdeskShiftStateV1>,
        transition_id: &str,
    ) -> Result<bool> {
        ensure_safe_shift_selector(&reservation.run_id)?;
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == reservation.shift_id)
                .context("shift routine reservation has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == reservation.shift_id)
                .context("shift routine reservation has no stored arm receipt")?;
            reservation.validate_against(manifest, arm_receipt)?;
            if let Some(existing) = ledger
                .routine_run_reservations
                .iter()
                .find(|existing| existing.run_id == reservation.run_id)
            {
                ensure!(
                    existing == &reservation,
                    "shift routine reservation ID has different content"
                );
                return Ok(false);
            }
            ensure!(
                !ledger.routine_run_reservations.iter().any(|existing| {
                    existing.shift_id == reservation.shift_id
                        && !ledger
                            .routine_outcome_receipts
                            .iter()
                            .any(|outcome| outcome.run_id == existing.run_id)
                }),
                "shift has an unresolved routine reservation"
            );
            let run_count = ledger
                .routine_run_reservations
                .iter()
                .filter(|existing| existing.shift_id == reservation.shift_id)
                .count();
            ensure!(
                run_count < usize::from(manifest.budget.max_routine_runs),
                "shift routine run budget is exhausted"
            );
            let state_index = ledger
                .states
                .iter()
                .position(|state| state.shift_id == reservation.shift_id)
                .context("shift routine reservation has no current state")?;
            let previous = ledger.states[state_index].clone();
            match previous.status {
                OffdeskShiftStatusV1::Armed => {
                    let next = running_state
                        .context("first shift routine reservation requires Running state")?;
                    validate_offdesk_shift_state_transition(
                        &previous,
                        &next,
                        manifest,
                        arm_receipt,
                    )?;
                    let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                        transition_id,
                        Some(&previous),
                        &next,
                    )?;
                    transition.validate_after(&previous, manifest, arm_receipt)?;
                    ledger.states[state_index] = next;
                    ledger.transitions.push(transition);
                }
                OffdeskShiftStatusV1::Running => ensure!(
                    running_state.is_none(),
                    "Running shift must not repeat its initial transition"
                ),
                _ => anyhow::bail!("shift state does not permit a routine reservation"),
            }
            ledger.routine_run_reservations.push(reservation);
            Ok(true)
        })
    }

    pub(crate) fn complete_routine_run(
        &self,
        outcome: OffdeskShiftRoutineOutcomeReceiptV1,
        attention_state: Option<OffdeskShiftStateV1>,
        transition_id: &str,
    ) -> Result<bool> {
        ensure_safe_shift_selector(&outcome.receipt_id)?;
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == outcome.shift_id)
                .context("shift routine outcome has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == outcome.shift_id)
                .context("shift routine outcome has no stored arm receipt")?;
            let reservation = ledger
                .routine_run_reservations
                .iter()
                .find(|reservation| reservation.run_id == outcome.run_id)
                .context("shift routine outcome has no stored reservation")?;
            outcome.validate_against(reservation, manifest, arm_receipt)?;
            if let Some(existing) = ledger
                .routine_outcome_receipts
                .iter()
                .find(|existing| existing.run_id == outcome.run_id)
            {
                ensure!(
                    existing == &outcome,
                    "shift routine outcome already exists with different content"
                );
                return Ok(false);
            }
            ensure!(
                outcome.outcome.requires_attention() || attention_state.is_none(),
                "successful shift routine outcome must not carry an attention transition"
            );
            if let Some(next) = attention_state {
                let state_index = ledger
                    .states
                    .iter()
                    .position(|state| state.shift_id == outcome.shift_id)
                    .context("shift routine outcome has no current state")?;
                let previous = ledger.states[state_index].clone();
                ensure!(
                    previous.status == OffdeskShiftStatusV1::Running,
                    "shift routine failure requires a Running shift"
                );
                validate_offdesk_shift_state_transition(&previous, &next, manifest, arm_receipt)?;
                let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                    transition_id,
                    Some(&previous),
                    &next,
                )?;
                transition.validate_after(&previous, manifest, arm_receipt)?;
                ledger.states[state_index] = next;
                ledger.transitions.push(transition);
            } else if outcome.outcome.requires_attention() {
                let current = ledger
                    .states
                    .iter()
                    .find(|state| state.shift_id == outcome.shift_id)
                    .context("shift routine outcome has no current state")?;
                ensure!(
                    current.status != OffdeskShiftStatusV1::Running,
                    "failed shift routine outcome must make a Running shift require attention"
                );
            }
            ledger.routine_outcome_receipts.push(outcome);
            Ok(true)
        })
    }

    pub(crate) fn ensure_profile_root_identity(&self, expected: (u64, u64)) -> Result<()> {
        let root = open_exact_profile_root(&self.profile_root)?;
        ensure!(
            crate::process::filesystem_file_identity(&root.metadata()?) == Some(expected),
            "shift store and approval ledger use different profile root identities"
        );
        ensure_active_root(&self.profile_root, &root)
    }

    pub fn transition(&self, next: OffdeskShiftStateV1, transition_id: &str) -> Result<()> {
        ensure_safe_shift_selector(transition_id)?;
        self.mutate(|ledger| {
            let manifest = ledger
                .manifests
                .iter()
                .find(|manifest| manifest.shift_id == next.shift_id)
                .context("shift transition has no stored manifest")?;
            let arm_receipt = ledger
                .arm_receipts
                .iter()
                .find(|receipt| receipt.shift_id == next.shift_id)
                .context("shift transition has no stored arm receipt")?;
            let state_index = ledger
                .states
                .iter()
                .position(|state| state.shift_id == next.shift_id)
                .context("shift transition has no current state")?;
            let previous = ledger.states[state_index].clone();
            validate_offdesk_shift_state_transition(&previous, &next, manifest, arm_receipt)?;
            let transition = OffdeskShiftTransitionReceiptV1::from_state_transition(
                transition_id,
                Some(&previous),
                &next,
            )?;
            transition.validate_after(&previous, manifest, arm_receipt)?;
            ledger.states[state_index] = next;
            ledger.transitions.push(transition);
            Ok(())
        })
    }

    fn load_read_only(&self) -> Result<OffdeskShiftLedgerV1> {
        match fs::symlink_metadata(&self.profile_root) {
            Err(error) if error.kind() == ErrorKind::NotFound => {
                return Ok(OffdeskShiftLedgerV1::default());
            }
            Err(error) => return Err(error).context("inspect shift profile root"),
            Ok(metadata) => ensure!(
                metadata.is_dir() && !metadata.file_type().is_symlink(),
                "shift profile root is not one safe directory"
            ),
        }
        let root = open_exact_profile_root(&self.profile_root)?;
        let ledger = read_ledger_from_root(&root)?;
        ledger.validate_for_profile(&self.profile)?;
        ensure_active_root(&self.profile_root, &root)?;
        Ok(ledger)
    }

    fn mutate<T>(&self, change: impl FnOnce(&mut OffdeskShiftLedgerV1) -> Result<T>) -> Result<T> {
        ensure!(
            self.profile_root.is_dir(),
            "writable shift profile root does not exist"
        );
        let root = open_exact_profile_root(&self.profile_root)?;
        let lock = open_or_create_lock(&root)?;
        FileExt::lock_exclusive(&lock).context("lock shift ledger")?;
        let result = (|| -> Result<T> {
            ensure_active_root(&self.profile_root, &root)?;
            let mut ledger = read_ledger_from_root(&root)?;
            ledger.validate_for_profile(&self.profile)?;
            let output = change(&mut ledger)?;
            ledger.validate_for_profile(&self.profile)?;
            let bytes = serde_json::to_vec_pretty(&ledger)?;
            ensure!(
                bytes.len() <= MAX_LEDGER_BYTES,
                "shift ledger exceeds its byte budget"
            );
            replace_ledger(&root, &self.profile_root, &bytes)?;
            Ok(output)
        })();
        let _ = FileExt::unlock(&lock);
        result
    }
}

fn ensure_safe_shift_selector(value: &str) -> Result<()> {
    ensure!(
        !value.is_empty()
            && value.trim() == value
            && value.chars().count() <= 256
            && !value.chars().any(char::is_control),
        "shift selector is invalid"
    );
    ensure!(
        !value.contains(['/', '\\', '\0']),
        "shift selector contains an unsafe path character"
    );
    Ok(())
}

fn open_exact_profile_root(path: &Path) -> Result<File> {
    let path_metadata = fs::symlink_metadata(path)
        .with_context(|| format!("inspect shift profile root {}", path.display()))?;
    ensure!(
        path_metadata.is_dir() && !path_metadata.file_type().is_symlink(),
        "shift profile root is not one safe directory"
    );
    let root = crate::process::open_bounded_root(path)
        .with_context(|| format!("open shift profile root {}", path.display()))?;
    ensure!(
        crate::process::filesystem_file_identity(&path_metadata)
            == crate::process::filesystem_file_identity(&root.metadata()?),
        "shift profile root path and descriptor identities differ"
    );
    Ok(root)
}

fn ensure_active_root(path: &Path, root: &File) -> Result<()> {
    let reopened = crate::process::open_bounded_root(path)
        .with_context(|| format!("reopen shift profile root {}", path.display()))?;
    ensure!(
        crate::process::filesystem_file_identity(&reopened.metadata()?)
            == crate::process::filesystem_file_identity(&root.metadata()?),
        "active shift profile root changed during access"
    );
    Ok(())
}

fn open_or_create_lock(root: &File) -> Result<File> {
    let path = Path::new(LOCK_FILE);
    let file = match crate::process::create_new_bounded_regular_file(root, path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            crate::process::open_bounded_regular_file(root, path)
                .context("open shift ledger lock")?
        }
        Err(error) => return Err(error).context("create shift ledger lock"),
    };
    ensure_single_link_owner_only(&file, LOCK_FILE, true)?;
    Ok(file)
}

fn read_ledger_from_root(root: &File) -> Result<OffdeskShiftLedgerV1> {
    let directory_before = FilesystemObjectSnapshot::from_metadata(&root.metadata()?);
    let mut file =
        match crate::process::open_bounded_regular_file(root, Path::new(OFFDESK_SHIFT_LEDGER_FILE))
        {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                let directory_after = FilesystemObjectSnapshot::from_metadata(&root.metadata()?);
                ensure!(
                    directory_before == directory_after,
                    "shift ledger directory changed while absence was observed"
                );
                return Ok(OffdeskShiftLedgerV1::default());
            }
            Err(error) => return Err(error).context("open shift ledger"),
        };
    ensure_single_link_owner_only(&file, OFFDESK_SHIFT_LEDGER_FILE, false)?;
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take((MAX_LEDGER_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .context("read shift ledger")?;
    ensure!(
        bytes.len() <= MAX_LEDGER_BYTES,
        "shift ledger exceeds its byte budget"
    );
    let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(before == after, "shift ledger changed while read");
    ensure_active_ledger_snapshot(root, &after)?;
    let ledger: OffdeskShiftLedgerV1 =
        serde_json::from_slice(&bytes).context("parse shift ledger")?;
    ledger.validate()?;
    ensure_active_ledger_snapshot(root, &after)?;
    Ok(ledger)
}

fn ensure_active_ledger_snapshot(root: &File, expected: &FilesystemObjectSnapshot) -> Result<()> {
    let active =
        crate::process::open_bounded_regular_file(root, Path::new(OFFDESK_SHIFT_LEDGER_FILE))
            .context("reopen active shift ledger")?;
    ensure_single_link_owner_only(&active, OFFDESK_SHIFT_LEDGER_FILE, false)?;
    ensure!(
        FilesystemObjectSnapshot::from_metadata(&active.metadata()?) == *expected,
        "active shift ledger was replaced while read"
    );
    Ok(())
}

fn replace_ledger(root: &File, root_path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary_name = PathBuf::from(format!(
        ".{OFFDESK_SHIFT_LEDGER_FILE}.{}.tmp",
        Uuid::new_v4()
    ));
    let result = (|| -> Result<()> {
        let mut temporary = crate::process::create_new_bounded_regular_file(root, &temporary_name)
            .context("create shift ledger temporary file")?;
        set_owner_only(&temporary)?;
        temporary
            .write_all(bytes)
            .context("write shift ledger temporary file")?;
        temporary
            .sync_all()
            .context("sync shift ledger temporary file")?;
        drop(temporary);
        ensure_active_root(root_path, root)?;
        crate::process::replace_bounded_root_file(
            root,
            &temporary_name,
            Path::new(OFFDESK_SHIFT_LEDGER_FILE),
        )
        .context("atomically replace shift ledger")?;
        ensure_active_root(root_path, root)?;
        let committed = read_ledger_from_root(root)?;
        ensure!(
            serde_json::to_vec_pretty(&committed)? == bytes,
            "committed shift ledger bytes differ"
        );
        root.sync_all().context("sync shift ledger directory")?;
        ensure_active_root(root_path, root)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = crate::process::remove_bounded_root_file(root, &temporary_name);
    }
    result
}

fn ensure_single_link_owner_only(file: &File, label: &str, allow_fix: bool) -> Result<()> {
    let metadata = file
        .metadata()
        .with_context(|| format!("inspect {label}"))?;
    ensure!(metadata.is_file(), "{label} is not a regular file");
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(metadata.nlink() == 1, "{label} must have exactly one link");
        if metadata.permissions().mode() & 0o777 != 0o600 {
            if !allow_fix {
                bail!("{label} is not owner-only");
            }
            set_owner_only(file)?;
        }
    }
    Ok(())
}

fn set_owner_only(file: &File) -> Result<()> {
    #[cfg(unix)]
    file.set_permissions(fs::Permissions::from_mode(0o600))
        .context("set owner-only shift ledger permissions")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{
        offdesk_shift_arm_receipt_sha256, offdesk_shift_manifest_sha256, BackgroundRunnerKind,
        ExecutionBrief, OffdeskShiftAuthorityV1, OffdeskShiftBudgetV1,
        OffdeskShiftEnterprisePolicyV1, OffdeskShiftEnterpriseProfileKeyV1,
        OffdeskShiftRoutineBindingV1, OffdeskShiftRoutineKindV1,
        OffdeskShiftRoutineResourceBudgetV1, OffdeskShiftRoutineScheduleV1,
        OffdeskShiftTaskBindingV1, OffdeskShiftWindowV1, OffdeskTask, OffdeskTaskInput,
        OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA, OFFDESK_SHIFT_MANIFEST_SCHEMA,
        OFFDESK_SHIFT_REQUIRED_DENIALS, OFFDESK_SHIFT_STATE_SCHEMA,
    };
    use chrono::{TimeZone, Utc};

    fn at(hour: u32, minute: u32) -> chrono::DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 28, hour, minute, 0)
            .single()
            .expect("valid timestamp")
    }

    fn manifest() -> OffdeskShiftManifestV1 {
        let task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some("task_store_1".to_string()),
                request_id: "request_store_1".to_string(),
                project_key: "forager".to_string(),
                capability_id: "dispatch.runtime".to_string(),
                runner_kind: BackgroundRunnerKind::LocalTmux,
                command: "cargo test --lib".to_string(),
                workdir: "/workspace/forager".to_string(),
                execution_brief: Some(ExecutionBrief {
                    request_id: "request_store_1".to_string(),
                    task_id: "task_store_1".to_string(),
                    project_key: "forager".to_string(),
                    approved: true,
                    allowed_runtime_mutations: vec!["dispatch.runtime".to_string()],
                    allowed_canonical_mutations: Vec::new(),
                    fresh_until: Some(at(9, 0)),
                }),
                not_before: None,
                mutation_class: Some("dispatch.runtime".to_string()),
                artifact_refs: Vec::new(),
                implementation_packet: None,
                artifact_kind: None,
                agent_mode: None,
                provider_id: None,
                model: None,
                preview: String::new(),
                reason: String::new(),
                log_artifact_path: None,
                result_artifact_path: None,
            },
            at(0, 0),
        );
        OffdeskShiftManifestV1 {
            serial_selection: None,
            preparation_review: None,
            preparation_requirements: None,
            weekly_usage: None,
            schema: OFFDESK_SHIFT_MANIFEST_SCHEMA.to_string(),
            shift_id: "shift_store_1".to_string(),
            profile: "default".to_string(),
            created_at: at(0, 0),
            window: OffdeskShiftWindowV1 {
                starts_at: at(0, 0),
                stops_at: at(9, 0),
                timezone: "Asia/Seoul".to_string(),
            },
            plan_id: "plan_store_1".to_string(),
            plan_review_id: "review_store_1".to_string(),
            plan_source_sha256: "a".repeat(64),
            plan_review_sha256: "c".repeat(64),
            task_bindings: vec![OffdeskShiftTaskBindingV1::from_task(
                &task,
                &"b".repeat(64),
                at(0, 0),
            )
            .expect("task binding")],
            routine_bindings: vec![OffdeskShiftRoutineBindingV1 {
                routine_id: "tick".to_string(),
                routine_kind: OffdeskShiftRoutineKindV1::DispatchTick,
                schedule: OffdeskShiftRoutineScheduleV1::Interval {
                    starts_at: at(0, 0),
                    stops_at: at(9, 0),
                    every_minutes: 10,
                },
                project_key: None,
                wiki_profile: None,
                project_root_identity_sha256: None,
                project_root_path: None,
            }],
            budget: OffdeskShiftBudgetV1 {
                max_concurrent_tasks: 1,
                max_task_launches: 1,
                max_attempts_per_task: 1,
                max_routine_runs: 64,
            },
            enterprise_policy: Some(OffdeskShiftEnterprisePolicyV1::built_in(
                OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
            )),
            host_policy_attestation: None,
            read_policy: None,
            template_instantiation: None,
            routine_resource_budget: Some(OffdeskShiftRoutineResourceBudgetV1 {
                max_process_starts: 64,
                max_external_calls: 0,
                max_disk_bytes_written: 0,
                max_llm_tokens: 0,
                max_cost_micro_usd: 0,
                llm_cost_rate_micro_usd_per_million_tokens: 0,
            }),
            authority: OffdeskShiftAuthorityV1 {
                task_dispatch_requires_fresh_approval: true,
                task_launch_spec_is_immutable: true,
                wiki_writes_are_candidate_only: true,
                plan_revision_requires_review: true,
                acceptance_requires_ondesk_review: true,
                does_not_authorize: OFFDESK_SHIFT_REQUIRED_DENIALS
                    .iter()
                    .map(|value| (*value).to_string())
                    .collect(),
            },
        }
    }

    fn arm_receipt(manifest: &OffdeskShiftManifestV1) -> OffdeskShiftArmReceiptV1 {
        OffdeskShiftArmReceiptV1 {
            schema: OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA.to_string(),
            receipt_id: "arm_store_1".to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(manifest).expect("manifest hash"),
            approval_id: "approval_store_1".to_string(),
            approved_at: at(0, 0),
            expires_at: at(9, 0),
            approved_by: "operator".to_string(),
            task_dispatch_still_requires_fresh_approval: true,
        }
    }

    fn state(
        manifest: &OffdeskShiftManifestV1,
        receipt: &OffdeskShiftArmReceiptV1,
        status: OffdeskShiftStatusV1,
        sequence: u64,
        updated_at: chrono::DateTime<Utc>,
    ) -> OffdeskShiftStateV1 {
        OffdeskShiftStateV1 {
            schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: receipt.manifest_sha256.clone(),
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(receipt, manifest)
                .expect("arm receipt hash"),
            sequence,
            status,
            updated_at,
            reason: "store lifecycle test".to_string(),
            acceptance_receipt_id: None,
            acceptance_receipt_sha256: None,
        }
    }

    fn store() -> Result<(tempfile::TempDir, OffdeskShiftStore)> {
        let temp = tempfile::tempdir()?;
        let root = temp.path().join("profile");
        fs::create_dir(&root)?;
        Ok((temp, OffdeskShiftStore::for_root(root)))
    }

    #[test]
    fn missing_read_only_store_creates_nothing() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let root = temp.path().join("missing");
        let store = OffdeskShiftStore::for_root(root.clone());
        assert!(store.snapshot()?.records.is_empty());
        assert!(!root.exists());
        Ok(())
    }

    #[test]
    fn manifest_arm_and_transition_replay_exactly() -> Result<()> {
        let (_temp, store) = store()?;
        let manifest = manifest();
        assert!(store.save_manifest(manifest.clone())?);
        assert!(!store.save_manifest(manifest.clone())?);
        let mut retried_after_response_loss = manifest.clone();
        retried_after_response_loss.created_at = at(0, 1);
        assert!(!store.save_manifest(retried_after_response_loss)?);
        let mut changed = manifest.clone();
        changed.window.stops_at = at(8, 59);
        assert!(store.save_manifest(changed).is_err());
        let receipt = arm_receipt(&manifest);
        let armed = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Armed,
            1,
            at(0, 0),
        );
        assert!(store.arm_shift(receipt.clone(), armed.clone(), "transition_arm_1")?);
        assert!(!store.arm_shift(receipt.clone(), armed, "transition_arm_1")?);
        let running = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Running,
            2,
            at(0, 1),
        );
        store.transition(running.clone(), "transition_running_1")?;

        let snapshot = store.snapshot()?;
        let record = snapshot.latest().context("latest shift")?;
        assert_eq!(record.state.as_ref(), Some(&running));
        assert_eq!(record.transitions.len(), 2);
        assert_eq!(snapshot.active(), Some(record));
        Ok(())
    }

    #[test]
    fn invalid_or_duplicate_transitions_do_not_replace_state() -> Result<()> {
        let (_temp, store) = store()?;
        let manifest = manifest();
        store.save_manifest(manifest.clone())?;
        let receipt = arm_receipt(&manifest);
        store.arm_shift(
            receipt.clone(),
            state(
                &manifest,
                &receipt,
                OffdeskShiftStatusV1::Armed,
                1,
                at(0, 0),
            ),
            "transition_arm_1",
        )?;
        let skipped = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::ReviewReady,
            3,
            at(1, 0),
        );
        assert!(store.transition(skipped, "transition_bad").is_err());
        assert_eq!(
            store
                .snapshot()?
                .latest()
                .and_then(|record| record.state.as_ref())
                .map(|state| state.sequence),
            Some(1)
        );
        assert!(store
            .arm_shift(
                receipt.clone(),
                state(
                    &manifest,
                    &receipt,
                    OffdeskShiftStatusV1::Armed,
                    1,
                    at(0, 0),
                ),
                "transition_arm_duplicate",
            )
            .is_err());
        Ok(())
    }

    #[test]
    fn torn_arm_receipt_and_state_boundaries_fail_closed() -> Result<()> {
        let (_temp, store) = store()?;
        let manifest = manifest();
        store.save_manifest(manifest.clone())?;
        let receipt = arm_receipt(&manifest);
        store.arm_shift(
            receipt.clone(),
            state(
                &manifest,
                &receipt,
                OffdeskShiftStatusV1::Armed,
                1,
                at(0, 0),
            ),
            "transition_arm_1",
        )?;

        let ledger_path = store.profile_root.join(OFFDESK_SHIFT_LEDGER_FILE);
        let committed = fs::read(&ledger_path)?;

        let mut arm_receipt_only: serde_json::Value = serde_json::from_slice(&committed)?;
        arm_receipt_only["states"] = serde_json::Value::Array(Vec::new());
        arm_receipt_only["transitions"] = serde_json::Value::Array(Vec::new());
        fs::write(&ledger_path, serde_json::to_vec_pretty(&arm_receipt_only)?)?;
        assert!(store.snapshot().is_err());

        let mut state_without_receipt: serde_json::Value = serde_json::from_slice(&committed)?;
        state_without_receipt["arm_receipts"] = serde_json::Value::Array(Vec::new());
        fs::write(
            &ledger_path,
            serde_json::to_vec_pretty(&state_without_receipt)?,
        )?;
        assert!(store.snapshot().is_err());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn unsafe_permissions_and_hardlinks_fail_closed() -> Result<()> {
        use std::os::unix::fs::PermissionsExt;

        let (temp, store) = store()?;
        store.save_manifest(manifest())?;
        let ledger = store.profile_root.join(OFFDESK_SHIFT_LEDGER_FILE);
        fs::set_permissions(&ledger, fs::Permissions::from_mode(0o644))?;
        assert!(store.snapshot().is_err());

        fs::set_permissions(&ledger, fs::Permissions::from_mode(0o600))?;
        fs::hard_link(&ledger, temp.path().join("ledger-hardlink"))?;
        assert!(store.snapshot().is_err());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_ledger_is_never_followed() -> Result<()> {
        use std::os::unix::fs::symlink;

        let (temp, store) = store()?;
        let external = temp.path().join("external.json");
        fs::write(&external, b"{}")?;
        symlink(
            &external,
            store.profile_root.join(OFFDESK_SHIFT_LEDGER_FILE),
        )?;
        assert!(store.snapshot().is_err());
        assert_eq!(fs::read(&external)?, b"{}");
        Ok(())
    }
}
