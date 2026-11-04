//! HackWatch Scores — публичный реестр оценок заявок хакатона (NEAR Testnet).
//!
//! Жизненный цикл (v0.2):
//!   1. owner (деплоер) пишет свою оценку: set_score(project_id, score, note).
//!   2. ЛЮБОЙ аккаунт пишет crowd-голос: submit_score(project_id, score, note).
//!      Один голос на аккаунт на проект; повторный вызов перезаписывает СВОЙ голос.
//!   3. owner аттестует разбор: attest(project_id, evaluator, report_hash).
//!   4. Любой читает: get_score / get_all / get_crowd / get_crowd_all / get_attest.
//!   5. migrate() — #[init(ignore_state)]: поднимает старый state v0.1 без потерь.
//!
//! set_score/get_score/get_all ABI не менялись (v0.1) — клиент publish работает
//! без правок. Оценка 0..=100, запись только owner для set_score и attest.

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::store::LookupMap;
use near_sdk::{env, near, near_bindgen, require, AccountId, PanicOnDefault};

/// Ключ состояния, который пишет макрос #[near(contract_state)].
const STATE_KEY: &[u8] = b"STATE";
const MAX_NOTE_BYTES: usize = 200;
const MAX_EVALUATOR_BYTES: usize = 64;
const MAX_REPORT_HASH_BYTES: usize = 128;

#[derive(BorshDeserialize, BorshSerialize, Clone, serde::Serialize, serde::Deserialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct ScoreEntry {
    pub score: u8,
    pub note: String,
    pub ts_ms: u64,
}

/// Один голос участника толпы.
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct Vote {
    pub score: u8,
    pub note: String,
    pub ts_ms: u64,
}

/// Бюллетень проекта: голоса + агрегаты.
///
/// count/sum держим в шапке, потому что у near_sdk::store::LookupMap нет
/// итератора: среднее считается инкрементально при каждом submit.
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct CrowdBallot {
    votes: LookupMap<AccountId, Vote>,
    count: u32,
    sum: u64,
}

/// Ответ get_crowd: число голосов и среднее, умноженное на 10 (без float).
#[derive(serde::Serialize, serde::Deserialize)]
pub struct CrowdStats {
    pub count: u32,
    pub avg_x10: u32,
}

/// Аттестация разбора от owner.
#[derive(BorshDeserialize, BorshSerialize, Clone, serde::Serialize, serde::Deserialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct Attest {
    pub evaluator: String,
    pub report_hash: String,
    pub ts_ms: u64,
}

/// Состояние v0.1 — ровно для миграции (тот же порядок и типы полей).
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct LegacyState {
    owner: AccountId,
    scores: LookupMap<String, ScoreEntry>,
    order: Vec<String>,
}

/// Префикс бюллетеня. Self-delimiting (длина строки в теле ключа), чтобы два
/// разных project_id не могли дать один и тот же префикс.
fn ballot_prefix(project_id: &str) -> Vec<u8> {
    let mut buf = Vec::with_capacity(project_id.len() + 5);
    buf.push(b'v');
    buf.extend_from_slice(&(project_id.len() as u32).to_le_bytes());
    buf.extend_from_slice(project_id.as_bytes());
    buf
}

impl CrowdBallot {
    fn empty(project_id: &str) -> Self {
        Self {
            votes: LookupMap::new(ballot_prefix(project_id)),
            count: 0,
            sum: 0,
        }
    }

    fn cast(&mut self, account: AccountId, score: u8, note: String, ts_ms: u64) {
        let previous = self.votes.get(&account).map(|old| old.score as u64);
        match previous {
            Some(old) => self.sum = self.sum + score as u64 - old,
            None => {
                self.count += 1;
                self.sum += score as u64;
            }
        }
        self.votes.insert(account, Vote { score, note, ts_ms });
    }

    fn avg_x10(&self) -> u32 {
        if self.count == 0 {
            0
        } else {
            (self.sum * 10 / self.count as u64) as u32
        }
    }
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct HackwatchScores {
    owner: AccountId,
    scores: LookupMap<String, ScoreEntry>,
    order: Vec<String>,
    crowd: LookupMap<String, CrowdBallot>,
    crowd_order: Vec<String>,
    attestations: LookupMap<String, Attest>,
}

#[near_bindgen]
impl HackwatchScores {
    #[init]
    pub fn new() -> Self {
        Self {
            owner: env::predecessor_account_id(),
            scores: LookupMap::new(b"s"),
            order: Vec::new(),
            crowd: LookupMap::new(b"c"),
            crowd_order: Vec::new(),
            attestations: LookupMap::new(b"a"),
        }
    }

    /// Только owner: записать оценку проекта (0..=100) с заметкой.
    pub fn set_score(&mut self, project_id: String, score: u8, note: String) {
        require!(env::predecessor_account_id() == self.owner, "только owner пишет оценки");
        require!(score <= 100, "оценка вне диапазона 0..=100");
        require!(!project_id.trim().is_empty(), "project_id пуст");
        let is_new = self.scores.get(&project_id).is_none();
        self.scores.insert(project_id.clone(), ScoreEntry { score, note, ts_ms: env::block_timestamp_ms() });
        if is_new {
            self.order.push(project_id);
        }
        env::log_str("score_set");
    }

    /// Оценка проекта, если записана.
    pub fn get_score(&self, project_id: String) -> Option<ScoreEntry> {
        self.scores.get(&project_id).cloned()
    }

    /// Весь рейтинг как список (project_id, score, note, ts_ms).
    /// Неотсортирован: сортировку делает читатель/клиент.
    pub fn get_all(&self) -> Vec<(String, ScoreEntry)> {
        self.order.iter().filter_map(|k| {
            self.scores.get(k).cloned().map(|v| (k.clone(), v))
        }).collect()
    }

    /// Любой аккаунт: голос толпы (0..=100). Повторный вызов тем же аккаунтом
    /// перезаписывает его собственный голос, чужие не трогает.
    pub fn submit_score(&mut self, project_id: String, score: u8, note: String) {
        require!(score <= 100, "crowd: оценка вне диапазона 0..=100");
        require!(!project_id.trim().is_empty(), "crowd: project_id пуст");
        require!(note.len() <= MAX_NOTE_BYTES, "crowd: заметка длиннее 200 байт");
        let account = env::predecessor_account_id();
        let ts_ms = env::block_timestamp_ms();
        let is_new_project = !self.crowd.contains_key(&project_id);
        let ballot = self
            .crowd
            .entry(project_id.clone())
            .or_insert_with_key(|id| CrowdBallot::empty(id));
        ballot.cast(account, score, note, ts_ms);
        if is_new_project {
            self.crowd_order.push(project_id);
        }
        env::log_str("crowd_score_set");
    }

    /// Статистика толпы по проекту; None — голосов не было.
    pub fn get_crowd(&self, project_id: String) -> Option<CrowdStats> {
        self.crowd.get(&project_id).map(|ballot| CrowdStats {
            count: ballot.count,
            avg_x10: ballot.avg_x10(),
        })
    }

    /// Вся толпа как (project_id, count, avg_x10) в порядке первого голоса.
    pub fn get_crowd_all(&self) -> Vec<(String, u32, u32)> {
        self.crowd_order.iter().filter_map(|k| {
            self.crowd.get(k).map(|b| (k.clone(), b.count, b.avg_x10()))
        }).collect()
    }

    /// Только owner: аттестовать разбор проекта (отчёт по хешу).
    pub fn attest(&mut self, project_id: String, evaluator: String, report_hash: String) {
        require!(env::predecessor_account_id() == self.owner, "только owner пишет аттестации");
        require!(!project_id.trim().is_empty(), "project_id пуст");
        require!(evaluator.len() <= MAX_EVALUATOR_BYTES, "evaluator длиннее 64 байт");
        require!(report_hash.len() <= MAX_REPORT_HASH_BYTES, "report_hash длиннее 128 байт");
        self.attestations.insert(project_id, Attest {
            evaluator,
            report_hash,
            ts_ms: env::block_timestamp_ms(),
        });
        env::log_str("attested");
    }

    /// Аттестация проекта, если записана.
    pub fn get_attest(&self, project_id: String) -> Option<Attest> {
        self.attestations.get(&project_id).cloned()
    }

    /// Апгрейд с v0.1: читаем старое состояние, отдаём новое с пустой толпой
    /// и пустыми аттестациями. Оценки owner не переписываются — LookupMap
    /// сериализует только префикс, записи остаются по старым ключам.
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let raw = env::storage_read(STATE_KEY)
            .unwrap_or_else(|| env::panic_str("migrate: старое состояние не найдено"));
        let legacy: LegacyState = near_sdk::borsh::from_slice(&raw)
            .unwrap_or_else(|_| env::panic_str("migrate: старое состояние не читается"));
        require!(env::predecessor_account_id() == legacy.owner, "migrate: только owner");
        Self {
            owner: legacy.owner,
            scores: legacy.scores,
            order: legacy.order,
            crowd: LookupMap::new(b"c"),
            crowd_order: Vec::new(),
            attestations: LookupMap::new(b"a"),
        }
    }
}

#[cfg(test)]
mod tests {
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::{testing_env, AccountId};

    use super::*;

    fn owner() -> AccountId {
        "owner.testnet".parse().unwrap()
    }

    fn stranger() -> AccountId {
        "stranger.testnet".parse().unwrap()
    }

    fn alice() -> AccountId {
        "alice.testnet".parse().unwrap()
    }

    fn bob() -> AccountId {
        "bob.testnet".parse().unwrap()
    }

    fn carol() -> AccountId {
        "carol.testnet".parse().unwrap()
    }

    fn ctx(account: AccountId) -> VMContextBuilder {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(account);
        b
    }

    /// Новый экземпляр поверх того же mock-хранилища — имитация следующего
    /// ончейн-вызова (предыдущий уже отработал и выгрузил state).
    fn reopen() -> HackwatchScores {
        HackwatchScores {
            owner: owner(),
            scores: LookupMap::new(b"s"),
            order: Vec::new(),
            crowd: LookupMap::new(b"c"),
            crowd_order: Vec::new(),
            attestations: LookupMap::new(b"a"),
        }
    }

    #[test]
    fn owner_sets_and_reads_score() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        c.set_score("proj-1".into(), 9, "demo ok".into());

        let s = c.get_score("proj-1".into()).expect("есть оценка");
        assert_eq!(s.score, 9);
        assert_eq!(s.note, "demo ok");
    }

    #[test]
    fn get_all_returns_entries() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        c.set_score("p1".into(), 9, "a".into());
        c.set_score("p2".into(), 4, "b".into());

        let all = c.get_all();
        assert_eq!(all.len(), 2);
        let names: Vec<_> = all.iter().map(|(k, _)| k.as_str()).collect();
        assert!(names.contains(&"p1") && names.contains(&"p2"));
    }

    #[test]
    #[should_panic(expected = "только owner")]
    fn stranger_cannot_set_score() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        testing_env!(ctx(stranger()).build());
        c.set_score("p".into(), 5, "x".into());
    }

    #[test]
    #[should_panic(expected = "оценка вне диапазона")]
    fn score_out_of_range_rejected() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        c.set_score("p".into(), 101, "x".into());
    }

    #[test]
    fn missing_score_returns_none() {
        testing_env!(ctx(owner()).build());
        let c = HackwatchScores::new();
        assert!(c.get_score("no-such".into()).is_none());
    }

    // ---------- v0.2: толпа ----------

    #[test]
    fn stranger_can_submit_crowd_score() {
        testing_env!(ctx(owner()).build());
        let c = HackwatchScores::new();
        testing_env!(ctx(stranger()).build());

        let mut c = c;
        c.submit_score("proj-1".into(), 55, "глазами ок".into());

        let stats = c.get_crowd("proj-1".into()).expect("голос есть");
        assert_eq!(stats.count, 1);
        assert_eq!(stats.avg_x10, 550);
        // owner-оценка от чужого submit не появляется:
        assert!(c.get_score("proj-1".into()).is_none());
    }

    #[test]
    fn crowd_repeat_submit_overwrites_own_vote() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();

        c.submit_score("p".into(), 80, "первая".into());
        c.submit_score("p".into(), 40, "вторая".into());

        let stats = c.get_crowd("p".into()).expect("голос есть");
        assert_eq!(stats.count, 1, "один аккаунт — один голос");
        assert_eq!(stats.avg_x10, 400, "повтор перезаписывает СВОЙ голос");
    }

    #[test]
    fn crowd_avg_x10_is_mean_times_ten() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();

        c.submit_score("p".into(), 80, "a".into());
        testing_env!(ctx(bob()).build());
        c.submit_score("p".into(), 90, "b".into());
        testing_env!(ctx(carol()).build());
        c.submit_score("p".into(), 100, "c".into());

        let stats = c.get_crowd("p".into()).expect("голоса есть");
        assert_eq!(stats.count, 3);
        assert_eq!(stats.avg_x10, 900, "(80+90+100)/3 = 90, x10 = 900");
    }

    #[test]
    fn crowd_ballot_survives_between_calls() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();
        c.submit_score("p".into(), 80, "a".into());
        drop(c);

        testing_env!(ctx(bob()).build());
        let mut c = reopen();
        c.submit_score("p".into(), 100, "b".into());
        drop(c);

        testing_env!(ctx(alice()).build());
        let mut c = reopen();
        c.submit_score("p".into(), 60, "a2".into());

        let stats = c.get_crowd("p".into()).expect("голоса есть");
        assert_eq!(stats.count, 2, "голоса разных аккаунтов дожили до следующего вызова");
        assert_eq!(stats.avg_x10, 800, "(60+100)/2 = 80, x10 = 800");
    }

    #[test]
    fn get_crowd_all_lists_each_project_once() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();

        c.submit_score("p1".into(), 100, "a".into());
        c.submit_score("p1".into(), 100, "a2".into());
        c.submit_score("p2".into(), 60, "b".into());

        let all = c.get_crowd_all();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0], ("p1".into(), 1u32, 1000u32));
        assert_eq!(all[1].0, "p2");
        assert_eq!(all[1].1, 1);
        assert_eq!(all[1].2, 600);
    }

    #[test]
    fn crowd_absent_project_returns_none() {
        testing_env!(ctx(alice()).build());
        let c = HackwatchScores::new();
        assert!(c.get_crowd("no-such".into()).is_none());
        assert!(c.get_crowd_all().is_empty());
    }

    #[test]
    #[should_panic(expected = "crowd: оценка вне диапазона")]
    fn crowd_rejects_score_above_100() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();
        c.submit_score("p".into(), 101, "x".into());
    }

    #[test]
    #[should_panic(expected = "crowd: заметка длиннее 200 байт")]
    fn crowd_rejects_oversize_note() {
        testing_env!(ctx(alice()).build());
        let mut c = HackwatchScores::new();
        c.submit_score("p".into(), 50, "x".repeat(201));
    }

    // ---------- v0.2: аттестации ----------

    #[test]
    fn owner_attests_and_get_attest_reads_it_back() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();

        c.attest("proj-1".into(), "mercury-3".into(), "a".repeat(64));

        let a = c.get_attest("proj-1".into()).expect("аттестация есть");
        assert_eq!(a.evaluator, "mercury-3");
        assert_eq!(a.report_hash.len(), 64);
    }

    #[test]
    #[should_panic(expected = "только owner пишет аттестации")]
    fn non_owner_cannot_attest() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        testing_env!(ctx(stranger()).build());
        c.attest("p".into(), "mercury-3".into(), "b".repeat(64));
    }

    #[test]
    #[should_panic(expected = "report_hash длиннее 128 байт")]
    fn attest_rejects_oversize_hash() {
        testing_env!(ctx(owner()).build());
        let mut c = HackwatchScores::new();
        c.attest("p".into(), "mercury-3".into(), "c".repeat(129));
    }

    // ---------- v0.2: миграция ----------

    #[test]
    fn migrate_preserves_all_owner_scores() {
        testing_env!(ctx(owner()).build());

        // Ровно то, что лежит на hackwatch.yksunder.testnet после v0.1: 21 запись.
        let mut legacy = LegacyState {
            owner: owner(),
            scores: LookupMap::new(b"s"),
            order: Vec::new(),
        };
        for i in 0..21u8 {
            let id = format!("proj-{}", i);
            legacy.scores.insert(id.clone(), ScoreEntry {
                score: i * 4,
                note: "v0.1".into(),
                ts_ms: 1_700_000_000_000,
            });
            legacy.order.push(id);
        }
        legacy.scores.flush();
        env::storage_write(STATE_KEY, &near_sdk::borsh::to_vec(&legacy).unwrap());
        drop(legacy);

        let mut c = HackwatchScores::migrate();

        let all = c.get_all();
        assert_eq!(all.len(), 21, "миграция не теряет записи v0.1");
        assert_eq!(c.get_score("proj-20".into()).expect("есть").score, 80);
        assert_eq!(c.get_score("proj-0".into()).expect("есть").score, 0);
        assert!(c.get_crowd("proj-0".into()).is_none(), "толпа пуста после миграции");
        assert!(c.get_attest("proj-0".into()).is_none(), "аттестации пусты");
        assert!(c.get_crowd_all().is_empty());

        // И поверх мигрированного state можно писать как раньше.
        c.submit_score("proj-0".into(), 70, "crowd после migrate".into());
        let stats = c.get_crowd("proj-0".into()).expect("голос есть");
        assert_eq!((stats.count, stats.avg_x10), (1, 700));
        assert_eq!(c.get_all().len(), 21, "owner-рейтинг не изменился");
    }

    #[test]
    #[should_panic(expected = "migrate: только owner")]
    fn migrate_by_stranger_is_refused() {
        testing_env!(ctx(owner()).build());
        let legacy = LegacyState {
            owner: owner(),
            scores: LookupMap::new(b"s"),
            order: Vec::new(),
        };
        env::storage_write(STATE_KEY, &near_sdk::borsh::to_vec(&legacy).unwrap());
        drop(legacy);

        testing_env!(ctx(stranger()).build());
        let _ = HackwatchScores::migrate();
    }
}
