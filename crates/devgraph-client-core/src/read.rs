use crate::protocol::{canonical_json, identifier, kind};
use crate::{
    decode_work, json, response_value, Error, ResponseMetadata, Result, Work, CYPHER_LIMIT,
    PAGE_LIMIT, WORK_LIMIT,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkFilters {
    #[serde(default)]
    pub include_archived: bool,
    #[serde(default)]
    pub descending: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct Subject {
    #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
    pub kind: String,
    pub id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ExpectedResult {
    pub columns: Vec<String>,
    #[cfg_attr(
        feature = "declarations",
        ts(type = "Array<\"string\" | \"integer\" | \"boolean\">")
    )]
    pub types: Vec<String>,
    pub limit: u16,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(untagged)]
pub enum CypherScalar {
    String(String),
    Integer(i64),
    Boolean(bool),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct CypherResult {
    #[cfg_attr(
        feature = "declarations",
        ts(type = "\"devgraph.cypher-read-result.v1\"")
    )]
    pub schema: String,
    pub columns: Vec<String>,
    pub rows: Vec<Vec<CypherScalar>>,
    #[cfg_attr(feature = "declarations", ts(type = "bigint"))]
    pub row_count: u16,
    #[cfg_attr(feature = "declarations", ts(type = "bigint"))]
    pub limit: u16,
}
fn default_limit() -> u16 {
    50
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ReadInput {
    GetTodo {
        #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
        work_kind: String,
        id: String,
    },
    GetWork {
        #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
        work_kind: String,
        id: String,
    },
    ListWork {
        #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
        work_kind: String,
        #[serde(default)]
        filters: WorkFilters,
        #[serde(default = "default_limit")]
        limit: u16,
        #[serde(default)]
        #[cfg_attr(feature="declarations", ts(optional = nullable))]
        after_id: Option<String>,
    },
    ListRelations {
        subject: Subject,
        #[cfg_attr(
            feature = "declarations",
            ts(
                type = "\"children\" | \"parent\" | \"dependencies\" | \"dependents\" | \"blockers\" | \"blocked\""
            )
        )]
        relation_kind: String,
        #[serde(default = "default_limit")]
        limit: u16,
        #[serde(default)]
        #[cfg_attr(feature="declarations", ts(optional = nullable))]
        after_resource: Option<String>,
    },
    Cypher {
        request_b64: String,
        expected_result: ExpectedResult,
    },
}

#[derive(Debug, Clone)]
pub struct PreparedRead {
    input: ReadInput,
    path: String,
    body: Option<Vec<u8>>,
    maximum: usize,
}
pub fn prepare_read(raw: &[u8]) -> Result<PreparedRead> {
    let value = json::parse(raw, 65_536, 16)?;
    let fields: &[&str] = match value["kind"].as_str() {
        Some("get_work" | "get_todo") => &["kind", "work_kind", "id"],
        Some("list_work") => &["kind", "work_kind", "filters", "limit", "after_id"],
        Some("list_relations") => &[
            "kind",
            "subject",
            "relation_kind",
            "limit",
            "after_resource",
        ],
        Some("cypher") => &["kind", "request_b64", "expected_result"],
        _ => return Err(invalid()),
    };
    if value
        .as_object()
        .is_none_or(|object| object.keys().any(|key| !fields.contains(&key.as_str())))
    {
        return Err(invalid());
    }
    let input: ReadInput =
        serde_json::from_value(value).map_err(|_| Error::validation("invalid_read_descriptor"))?;
    PreparedRead::new(input)
}
fn invalid() -> Error {
    Error::validation("invalid_read_descriptor")
}
fn limit_ok(limit: u16) -> bool {
    (1..=100).contains(&limit)
}
fn resource_valid(resource: &str) -> bool {
    resource
        .split_once('/')
        .is_some_and(|(k, id)| kind(k) && identifier(id))
}
fn safe_name(value: &str) -> bool {
    let b = value.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && b[0].is_ascii_alphabetic()
        && b.iter().all(|b| b.is_ascii_alphanumeric() || *b == b'_')
}
impl PreparedRead {
    pub fn new(input: ReadInput) -> Result<Self> {
        let (path, body, maximum) = match &input {
            ReadInput::GetTodo { work_kind, id } => {
                if !(kind(work_kind) || work_kind == "Todo") || !identifier(id) {
                    return Err(invalid());
                }
                (format!("/todos/v2/{work_kind}/{id}"), None, WORK_LIMIT)
            }
            ReadInput::GetWork { work_kind, id } => {
                if !kind(work_kind) || !identifier(id) {
                    return Err(invalid());
                }
                (format!("/work/{work_kind}/{id}"), None, WORK_LIMIT)
            }
            ReadInput::ListWork {
                work_kind,
                filters,
                limit,
                after_id,
            } => {
                if !kind(work_kind)
                    || !limit_ok(*limit)
                    || after_id.as_ref().is_some_and(|id| !identifier(id))
                {
                    return Err(invalid());
                }
                let mut path = format!(
                    "/work/{work_kind}?include_archived={}&descending={}&limit={limit}",
                    filters.include_archived, filters.descending
                );
                if let Some(id) = after_id {
                    path.push_str(&format!("&after_id={id}"));
                }
                (path, None, PAGE_LIMIT)
            }
            ReadInput::ListRelations {
                subject,
                relation_kind,
                limit,
                after_resource,
            } => {
                if !kind(&subject.kind)
                    || !identifier(&subject.id)
                    || !limit_ok(*limit)
                    || ![
                        "children",
                        "parent",
                        "dependencies",
                        "dependents",
                        "blockers",
                        "blocked",
                    ]
                    .contains(&relation_kind.as_str())
                    || (matches!(relation_kind.as_str(), "blockers" | "blocked")
                        && subject.kind != "Task")
                    || after_resource.as_ref().is_some_and(|v| !resource_valid(v))
                {
                    return Err(invalid());
                }
                let mut path = format!(
                    "/work/{}/{}/relationships/{relation_kind}?limit={limit}",
                    subject.kind, subject.id
                );
                if let Some(value) = after_resource {
                    path.push_str(&format!("&after_resource={}", value.replace('/', "%2F")));
                }
                (path, None, PAGE_LIMIT)
            }
            ReadInput::Cypher {
                request_b64,
                expected_result,
            } => {
                if request_b64.len() > 43_691 {
                    return Err(invalid());
                }
                let raw = URL_SAFE_NO_PAD.decode(request_b64).map_err(|_| invalid())?;
                if URL_SAFE_NO_PAD.encode(&raw) != *request_b64 {
                    return Err(invalid());
                }
                let value = json::parse(&raw, 32_768, 4)?;
                let object = value.as_object().ok_or_else(invalid)?;
                if object.len() != 3
                    || value["schema"] != "devgraph.cypher-read-request.v1"
                    || !value["query"]
                        .as_str()
                        .is_some_and(|s| !s.is_empty() && s.len() <= 8192 && s.is_ascii())
                    || !value["parameters"].as_object().is_some_and(|p| {
                        p.len() <= 32
                            && p.iter().all(|(k, v)| {
                                safe_name(k)
                                    && (v.is_boolean()
                                        || v.as_i64().is_some()
                                        || v.as_str().is_some_and(|s| s.chars().count() <= 16_384))
                            })
                    })
                    || !limit_ok(expected_result.limit)
                    || expected_result.columns.is_empty()
                    || expected_result.columns.len() > 16
                    || expected_result.columns.len() != expected_result.types.len()
                    || expected_result.columns.iter().any(|c| !safe_name(c))
                    || expected_result
                        .columns
                        .iter()
                        .collect::<std::collections::BTreeSet<_>>()
                        .len()
                        != expected_result.columns.len()
                    || expected_result
                        .types
                        .iter()
                        .any(|t| !["string", "integer", "boolean"].contains(&t.as_str()))
                {
                    return Err(invalid());
                }
                let body = canonical_json(&value).map_err(|_| invalid())?;
                if body.len() > 32_768 {
                    return Err(invalid());
                }
                ("/query/cypher".into(), Some(body), CYPHER_LIMIT)
            }
        };
        Ok(Self {
            input,
            path,
            body,
            maximum,
        })
    }
    pub fn method(&self) -> &str {
        if self.body.is_some() {
            "POST"
        } else {
            "GET"
        }
    }
    pub fn path(&self) -> &str {
        &self.path
    }
    pub fn body(&self) -> Option<&[u8]> {
        self.body.as_deref()
    }
    pub fn max_response_bytes(&self) -> usize {
        self.maximum
    }
    pub fn input(&self) -> &ReadInput {
        &self.input
    }
    pub fn decode_response(&self, raw: &[u8], metadata: &ResponseMetadata) -> Result<Value> {
        let value = response_value(raw, metadata, self.maximum, false)?;
        if metadata.status != 200 {
            return Err(Error::protocol("read_status_mismatch"));
        }
        match &self.input {
            ReadInput::GetTodo { work_kind, id } => {
                let work: crate::ProgressWork = serde_json::from_value(value.clone())
                    .map_err(|_| Error::protocol("invalid_todo_result"))?;
                if &work.kind != work_kind
                    || &work.id != id
                    || work.key != format!("{work_kind}/{id}")
                    || work.classification_required != work.progress.is_none()
                    || ["progress", "workflow_id", "stage", "parent"]
                        .iter()
                        .any(|key| value.get(key).is_none())
                {
                    return Err(Error::protocol("todo_identity_mismatch"));
                }
            }
            ReadInput::GetWork { work_kind, id } => {
                let work = decode_work(value.clone())?;
                if &work.kind != work_kind || &work.id != id {
                    return Err(Error::protocol("work_identity_mismatch"));
                }
            }
            ReadInput::ListWork {
                work_kind,
                filters,
                limit,
                after_id,
            } => {
                let items = page(&value, *limit)?;
                let mut prior = after_id.as_deref();
                for work in &items {
                    if &work.kind != work_kind
                        || (!filters.include_archived && work.status == "archived")
                        || prior.is_some_and(|p| {
                            if filters.descending {
                                work.id.as_str() >= p
                            } else {
                                work.id.as_str() <= p
                            }
                        })
                    {
                        return Err(Error::protocol("page_order_mismatch"));
                    }
                    prior = Some(&work.id);
                }
            }
            ReadInput::ListRelations {
                relation_kind,
                limit,
                after_resource,
                ..
            } => {
                let items = page(&value, *limit)?;
                if relation_kind == "parent" && items.len() > 1 {
                    return Err(Error::protocol("invalid_parent_page"));
                }
                let mut prior = after_resource.clone();
                for work in &items {
                    let resource = format!("{}/{}", work.kind, work.id);
                    if prior.as_ref().is_some_and(|p| resource <= *p)
                        || (matches!(relation_kind.as_str(), "blockers" | "blocked")
                            && work.kind != "Task")
                    {
                        return Err(Error::protocol("page_order_mismatch"));
                    }
                    prior = Some(resource);
                }
            }
            ReadInput::Cypher {
                expected_result, ..
            } => {
                // The generated public declaration and actual result decoder
                // share this concrete schema, rather than a parallel TS shape.
                let result: CypherResult = serde_json::from_value(value.clone())
                    .map_err(|_| Error::protocol("cypher_result_mismatch"))?;
                if value.as_object().is_none_or(|v| v.len() != 5)
                    || value["schema"] != "devgraph.cypher-read-result.v1"
                    || value["columns"] != serde_json::to_value(&expected_result.columns).unwrap()
                    || value["limit"].as_u64() != Some(expected_result.limit.into())
                {
                    return Err(Error::protocol("cypher_result_mismatch"));
                }
                if result.rows.len() != result.row_count as usize {
                    return Err(Error::protocol("cypher_result_mismatch"));
                }
                let rows = value["rows"]
                    .as_array()
                    .ok_or(Error::protocol("cypher_result_mismatch"))?;
                if rows.len() > expected_result.limit.into()
                    || value["row_count"].as_u64() != Some(rows.len() as u64)
                {
                    return Err(Error::protocol("cypher_result_mismatch"));
                }
                for row in rows {
                    let cells = row
                        .as_array()
                        .ok_or(Error::protocol("cypher_result_mismatch"))?;
                    if cells.len() != expected_result.types.len()
                        || cells
                            .iter()
                            .zip(&expected_result.types)
                            .any(|(v, t)| match t.as_str() {
                                "string" => v.as_str().is_none_or(|s| s.chars().count() > 16_384),
                                "integer" => v.as_i64().is_none(),
                                "boolean" => !v.is_boolean(),
                                _ => true,
                            })
                    {
                        return Err(Error::protocol("cypher_result_mismatch"));
                    }
                }
            }
        }
        Ok(value)
    }
}
fn page(value: &Value, limit: u16) -> Result<Vec<Work>> {
    if value.as_object().is_none_or(|v| v.len() != 1) {
        return Err(Error::protocol("invalid_work_page"));
    }
    let items = value["items"]
        .as_array()
        .ok_or(Error::protocol("invalid_work_page"))?;
    if items.len() > limit as usize {
        return Err(Error::protocol("page_limit_mismatch"));
    }
    items.iter().cloned().map(decode_work).collect()
}
