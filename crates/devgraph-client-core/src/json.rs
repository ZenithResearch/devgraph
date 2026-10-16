//! Bounded raw JSON parsing before values can pass through a JS number.
//! Integer tokens are parsed directly to i64/u64, independently of serde features.
use crate::{Error, Result};
use serde_json::{Map, Value};

pub fn parse(raw: &[u8], maximum: usize, depth: usize) -> Result<Value> {
    if raw.len() > maximum {
        return Err(Error::limit("response_too_large"));
    }
    let mut parser = Parser {
        raw,
        at: 0,
        maximum_depth: depth,
    };
    let value = parser.value(0)?;
    parser.space();
    if parser.at != raw.len() {
        return Err(Error::protocol("invalid_json"));
    }
    Ok(value)
}

struct Parser<'a> {
    raw: &'a [u8],
    at: usize,
    maximum_depth: usize,
}
impl Parser<'_> {
    fn space(&mut self) {
        while self
            .raw
            .get(self.at)
            .is_some_and(|b| b" \r\n\t".contains(b))
        {
            self.at += 1;
        }
    }
    fn eat(&mut self, byte: u8) -> bool {
        self.space();
        if self.raw.get(self.at) == Some(&byte) {
            self.at += 1;
            true
        } else {
            false
        }
    }
    fn value(&mut self, depth: usize) -> Result<Value> {
        if depth > self.maximum_depth {
            return Err(Error::limit("response_too_deep"));
        }
        self.space();
        match self.raw.get(self.at).copied() {
            Some(b'"') => self.string().map(Value::String),
            Some(b'{') => {
                self.at += 1;
                let mut result = Map::new();
                if self.eat(b'}') {
                    return Ok(Value::Object(result));
                }
                loop {
                    self.space();
                    let key = self.string()?;
                    if !self.eat(b':') {
                        return Err(Error::protocol("invalid_json"));
                    }
                    let value = self.value(depth + 1)?;
                    if result.insert(key, value).is_some() {
                        return Err(Error::protocol("duplicate_json_key"));
                    }
                    if self.eat(b'}') {
                        break;
                    }
                    if !self.eat(b',') {
                        return Err(Error::protocol("invalid_json"));
                    }
                }
                Ok(Value::Object(result))
            }
            Some(b'[') => {
                self.at += 1;
                let mut result = Vec::new();
                if self.eat(b']') {
                    return Ok(Value::Array(result));
                }
                loop {
                    result.push(self.value(depth + 1)?);
                    if self.eat(b']') {
                        break;
                    }
                    if !self.eat(b',') {
                        return Err(Error::protocol("invalid_json"));
                    }
                }
                Ok(Value::Array(result))
            }
            Some(b't') => self.literal(b"true", Value::Bool(true)),
            Some(b'f') => self.literal(b"false", Value::Bool(false)),
            Some(b'n') => self.literal(b"null", Value::Null),
            Some(b'-' | b'0'..=b'9') => self.integer(),
            _ => Err(Error::protocol("invalid_json")),
        }
    }
    fn literal(&mut self, expected: &[u8], value: Value) -> Result<Value> {
        if self.raw.get(self.at..self.at + expected.len()) != Some(expected) {
            return Err(Error::protocol("invalid_json"));
        }
        self.at += expected.len();
        Ok(value)
    }
    fn string(&mut self) -> Result<String> {
        if self.raw.get(self.at) != Some(&b'"') {
            return Err(Error::protocol("invalid_json"));
        }
        let start = self.at;
        self.at += 1;
        while let Some(&byte) = self.raw.get(self.at) {
            self.at += 1;
            match byte {
                b'"' => {
                    return serde_json::from_slice(&self.raw[start..self.at])
                        .map_err(|_| Error::protocol("invalid_json_string"))
                }
                b'\\' => {
                    // The JSON string decoder validates escape grammar and surrogate pairs.
                    if self.at >= self.raw.len() {
                        break;
                    }
                    self.at += 1;
                }
                0..=31 => return Err(Error::protocol("invalid_json_string")),
                _ => (),
            }
        }
        Err(Error::protocol("invalid_json_string"))
    }
    fn integer(&mut self) -> Result<Value> {
        let start = self.at;
        let negative = self.raw[self.at] == b'-';
        if negative {
            self.at += 1;
        }
        let digits = self.at;
        while self.raw.get(self.at).is_some_and(u8::is_ascii_digit) {
            self.at += 1;
        }
        if digits == self.at || (self.at - digits > 1 && self.raw[digits] == b'0') {
            return Err(Error::protocol("invalid_json_number"));
        }
        // Public Work and bounded Cypher contracts have integer numbers only.
        let token = std::str::from_utf8(&self.raw[start..self.at])
            .map_err(|_| Error::protocol("invalid_json_number"))?;
        if negative {
            token.parse::<i64>().map(Value::from)
        } else {
            token.parse::<u64>().map(Value::from)
        }
        .map_err(|_| Error::protocol("invalid_json_number"))
    }
}
