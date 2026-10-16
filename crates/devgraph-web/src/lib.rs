//! Small checked ABI: all protocol and response semantics live in shared Rust.
use devgraph_client_core::{self as core, Error, PreparedMutation, ResponseMetadata};
use js_sys::{Array, BigInt, JsString, Object, Reflect, Uint8Array};
use serde_json::{Map, Value};
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;

fn error(error: Error) -> JsValue {
    // Never preserve raw JS exceptions, request values or authority in diagnostics.
    to_js(&serde_json::to_value(error).unwrap(), false)
}
fn invalid() -> JsValue {
    error(Error::validation("invalid_js_value"))
}

fn checked_string(value: &JsValue, maximum: u32) -> Result<String, JsValue> {
    if !value.is_string() {
        return Err(invalid());
    }
    let string: &JsString = value.unchecked_ref();
    if string.length() > maximum {
        return Err(error(Error::limit("input_too_large")));
    }
    let mut i = 0;
    while i < string.length() {
        let unit = string.char_code_at(i) as u16;
        if (0xd800..=0xdbff).contains(&unit) {
            i += 1;
            if i >= string.length() || !(0xdc00..=0xdfff).contains(&(string.char_code_at(i) as u16))
            {
                return Err(error(Error::validation("invalid_utf16")));
            }
        } else if (0xdc00..=0xdfff).contains(&unit) {
            return Err(error(Error::validation("invalid_utf16")));
        }
        i += 1;
    }
    value.as_string().ok_or_else(invalid)
}

struct InputBudget {
    remaining: usize,
}
impl InputBudget {
    fn charge(&mut self, amount: usize) -> Result<(), JsValue> {
        self.remaining = self
            .remaining
            .checked_sub(amount)
            .ok_or_else(|| error(Error::limit("input_too_large")))?;
        Ok(())
    }
    fn value(&mut self, value: &JsValue, depth: usize) -> Result<Value, JsValue> {
        if depth > 16 {
            return Err(error(Error::validation("input_too_deep")));
        }
        self.charge(1)?;
        if value.is_null() {
            return Ok(Value::Null);
        }
        if let Some(value) = value.as_bool() {
            return Ok(Value::Bool(value));
        }
        if value.is_string() {
            let string = checked_string(value, 131_072)?;
            self.charge(string.len())?;
            return Ok(Value::String(string));
        }
        if value.is_bigint() {
            // TryFrom checks the original BigInt equals the narrowed value.
            let number = i64::try_from(value.clone()).map_err(|_| invalid())?;
            if number.unsigned_abs() > core::protocol::MAX_SAFE {
                return Err(invalid());
            }
            return Ok(Value::from(number));
        }
        if let Some(number) = value.as_f64() {
            if !number.is_finite()
                || number.fract() != 0.0
                || number.abs() > core::protocol::MAX_SAFE as f64
                || (number == 0.0 && number.is_sign_negative())
            {
                return Err(invalid());
            }
            return Ok(Value::from(number as i64));
        }
        if Array::is_array(value) {
            let array: &Array = value.unchecked_ref();
            if array.length() as usize > self.remaining {
                return Err(error(Error::limit("input_too_large")));
            }
            let keys = Reflect::own_keys(value).map_err(|_| invalid())?;
            // Dense own arrays only: no holes, symbols or hidden side fields.
            if keys.length() != array.length() + 1 {
                return Err(invalid());
            }
            let mut items = Vec::with_capacity(array.length() as usize);
            for i in 0..array.length() {
                items.push(self.value(
                    &own_data(value, &JsValue::from_str(&i.to_string()))?,
                    depth + 1,
                )?);
            }
            return Ok(Value::Array(items));
        }
        if !value.is_object() || value.is_function() {
            return Err(invalid());
        }
        let prototype = Reflect::get_prototype_of(value).map_err(|_| invalid())?;
        let plain_prototype = Object::get_prototype_of(&Object::new());
        if !prototype.is_null() && prototype != plain_prototype {
            return Err(invalid());
        }
        let keys = Reflect::own_keys(value).map_err(|_| invalid())?;
        if keys.length() as usize > self.remaining {
            return Err(error(Error::limit("input_too_large")));
        }
        let mut object = Map::new();
        for key in keys {
            let name = checked_string(&key, 131_072)?;
            self.charge(name.len())?;
            object.insert(name, self.value(&own_data(value, &key)?, depth + 1)?);
        }
        Ok(Value::Object(object))
    }
}
fn own_data(object: &JsValue, key: &JsValue) -> Result<JsValue, JsValue> {
    let descriptor = Reflect::get_own_property_descriptor(object.unchecked_ref::<Object>(), key)
        .map_err(|_| invalid())?;
    if descriptor.is_undefined()
        || Reflect::has(&descriptor, &"get".into()).map_err(|_| invalid())?
        || Reflect::has(&descriptor, &"set".into()).map_err(|_| invalid())?
        || Reflect::get(&descriptor, &"enumerable".into())
            .map_err(|_| invalid())?
            .as_bool()
            != Some(true)
    {
        return Err(invalid());
    }
    Reflect::get(&descriptor, &"value".into()).map_err(|_| invalid())
}
fn from_js(value: &JsValue) -> Result<Value, JsValue> {
    InputBudget { remaining: 262_144 }.value(value, 0)
}
fn raw_bytes(raw: &JsValue, maximum: usize) -> Result<Vec<u8>, JsValue> {
    let bytes = raw.dyn_ref::<Uint8Array>().ok_or_else(invalid)?;
    if bytes.length() as usize > maximum {
        return Err(error(Error::limit("response_too_large")));
    }
    Ok(bytes.to_vec())
}
fn metadata(value: &JsValue) -> Result<ResponseMetadata, JsValue> {
    serde_json::from_value(from_js(value)?).map_err(|_| invalid())
}
fn to_js(value: &Value, bigints: bool) -> JsValue {
    match value {
        Value::Null => JsValue::NULL,
        Value::Bool(v) => JsValue::from_bool(*v),
        Value::String(v) => JsValue::from_str(v),
        Value::Number(v) => {
            if bigints {
                if let Some(n) = v.as_i64() {
                    return BigInt::from(n).into();
                }
                if let Some(n) = v.as_u64() {
                    return BigInt::from(n).into();
                }
            }
            JsValue::from_f64(v.as_f64().unwrap())
        }
        Value::Array(items) => items
            .iter()
            .map(|v| to_js(v, bigints))
            .collect::<Array>()
            .into(),
        Value::Object(fields) => {
            let object = Object::new();
            for (key, value) in fields {
                // Defining own data avoids __proto__ setter semantics.
                let descriptor = Object::new();
                Reflect::set(&descriptor, &"value".into(), &to_js(value, bigints)).unwrap();
                Reflect::set(&descriptor, &"enumerable".into(), &JsValue::TRUE).unwrap();
                Reflect::set(&descriptor, &"writable".into(), &JsValue::TRUE).unwrap();
                Reflect::set(&descriptor, &"configurable".into(), &JsValue::TRUE).unwrap();
                Object::define_property(&object, &JsValue::from_str(key), &descriptor);
            }
            object.into()
        }
    }
}

#[wasm_bindgen]
pub fn abi_version() -> String {
    core::ABI_VERSION.into()
}
#[wasm_bindgen]
pub fn profile_digest(stable_issuer: JsValue) -> Result<String, JsValue> {
    core::receiver_profile_digest(&checked_string(&stable_issuer, 256)?).map_err(error)
}
#[wasm_bindgen]
pub struct PreparedRequest {
    inner: PreparedMutation,
}
#[wasm_bindgen]
impl PreparedRequest {
    pub fn canonical_bytes(&self) -> Uint8Array {
        Uint8Array::from(self.inner.canonical())
    }
    pub fn summary(&self) -> JsValue {
        to_js(&self.inner.summary(), true)
    }
    pub fn decode_credential_mutation(
        &self,
        raw: JsValue,
        meta: JsValue,
        reconcile: bool,
    ) -> Result<JsValue, JsValue> {
        let meta = metadata(&meta)?;
        let maximum = meta.body_limit(core::WORK_LIMIT).map_err(error)?;
        let result = self
            .inner
            .decode_credential_response(&raw_bytes(&raw, maximum)?, &meta, reconcile)
            .map_err(error)?;
        Ok(to_js(&result, true))
    }
    pub fn decode_mutation(&self, raw: JsValue, meta: JsValue) -> Result<JsValue, JsValue> {
        let meta = metadata(&meta)?;
        let maximum = meta.body_limit(core::WORK_LIMIT).map_err(error)?;
        let result = self
            .inner
            .decode_response_value(&raw_bytes(&raw, maximum)?, &meta)
            .map_err(error)?;
        Ok(to_js(&serde_json::to_value(result).unwrap(), true))
    }
}
#[wasm_bindgen]
pub fn prepare_bytes(raw: JsValue, key: JsValue) -> Result<PreparedRequest, JsValue> {
    let inner = PreparedMutation::parse(&raw_bytes(&raw, 131_072)?, &checked_string(&key, 128)?)
        .map_err(error)?;
    Ok(PreparedRequest { inner })
}
#[wasm_bindgen]
pub fn prepare_value(value: JsValue, key: JsValue) -> Result<PreparedRequest, JsValue> {
    let inner = PreparedMutation::from_value(&from_js(&value)?, &checked_string(&key, 128)?)
        .map_err(error)?;
    Ok(PreparedRequest { inner })
}
#[wasm_bindgen]
pub fn read_descriptor(input: JsValue) -> Result<JsValue, JsValue> {
    let raw = core::protocol::canonical_json(&from_js(&input)?).map_err(|_| invalid())?;
    let read = core::prepare_read(&raw).map_err(error)?;
    let value = serde_json::json!({"request":read.input(),"method":read.method(),"path":read.path(),
                                  "max_response_bytes":read.max_response_bytes()});
    let object = to_js(&value, false);
    let body = read
        .body()
        .map(|bytes| JsValue::from(Uint8Array::from(bytes)))
        .unwrap_or(JsValue::NULL);
    Reflect::set(&object, &"body".into(), &body).map_err(|_| invalid())?;
    Ok(object)
}
#[wasm_bindgen]
pub fn decode_read(raw: JsValue, meta: JsValue, input: JsValue) -> Result<JsValue, JsValue> {
    let descriptor = core::protocol::canonical_json(&from_js(&input)?).map_err(|_| invalid())?;
    let read = core::prepare_read(&descriptor).map_err(error)?;
    let meta = metadata(&meta)?;
    let maximum = meta.body_limit(read.max_response_bytes()).map_err(error)?;
    let value = read
        .decode_response(&raw_bytes(&raw, maximum)?, &meta)
        .map_err(error)?;
    Ok(to_js(&value, true))
}
