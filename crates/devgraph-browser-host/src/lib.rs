//! Chrome native messaging framing and connection-local closed Work state machine.
mod host;
use devgraph_client_native::{NativeError, Result, FRAME_BYTES};
pub use host::Host;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

/// Chrome uses a native-endian u32 byte count. Bound before allocating the frame.
pub async fn read_frame(reader: &mut (impl AsyncRead + Unpin)) -> Result<Option<Vec<u8>>> {
    let mut prefix = [0; 4];
    let first = reader
        .read(&mut prefix[..1])
        .await
        .map_err(|_| NativeError::new("invalid_frame", false))?;
    if first == 0 {
        return Ok(None);
    }
    reader
        .read_exact(&mut prefix[1..])
        .await
        .map_err(|_| NativeError::new("invalid_frame", false))?;
    let size = u32::from_ne_bytes(prefix) as usize;
    if size == 0 || size > FRAME_BYTES {
        return Err(NativeError::new("invalid_frame", false));
    }
    let mut bytes = vec![0; size];
    reader
        .read_exact(&mut bytes)
        .await
        .map_err(|_| NativeError::new("invalid_frame", false))?;
    Ok(Some(bytes))
}
pub async fn write_frame(
    writer: &mut (impl AsyncWrite + Unpin),
    value: &serde_json::Value,
) -> Result<()> {
    let bytes = devgraph_work_protocol::canonical_json(value)
        .map_err(|_| NativeError::new("invalid_frame", false))?;
    if bytes.len() > FRAME_BYTES {
        return Err(NativeError::new("invalid_frame", false));
    }
    writer
        .write_all(&(bytes.len() as u32).to_ne_bytes())
        .await
        .map_err(|_| NativeError::new("port_disconnected", false))?;
    writer
        .write_all(&bytes)
        .await
        .map_err(|_| NativeError::new("port_disconnected", false))?;
    writer
        .flush()
        .await
        .map_err(|_| NativeError::new("port_disconnected", false))
}

/// Serve one port with a bounded control task set. Saturation drops the port and
/// cancels in-flight work instead of queueing cancellation behind network waits.
pub async fn serve<R, W>(host: Host, input: R, output: W) -> Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
{
    let (send, mut receive) = tokio::sync::mpsc::channel(16);
    let mut writer = tokio::spawn(async move {
        let mut stdout = output;
        while let Some(value) = receive.recv().await {
            write_frame(&mut stdout, &value).await?;
        }
        Ok::<(), devgraph_client_native::NativeError>(())
    });
    let mut input = input;
    let mut controls = tokio::task::JoinSet::new();
    let shutdown = host.shutdown();
    let mut writer_finished = false;
    // Each command is independent: cancel/dispose can be read while network or
    // trusted issuance is in flight. The host itself enforces bounded slots.
    let outcome = loop {
        while controls.try_join_next().is_some() {}
        let frame = tokio::select! {
            frame=read_frame(&mut input)=>frame,
            _=shutdown.cancelled()=>break Err(devgraph_client_native::NativeError::new("port_disconnected",false)),
            _=&mut writer=>{ writer_finished = true; break Err(devgraph_client_native::NativeError::new("port_disconnected",false)); },
        };
        match frame {
            Ok(Some(raw)) => {
                // Saturation closes the connection and cancels everything; no
                // cancel or dispose command waits behind network-held capacity.
                if controls.len() >= 64 {
                    break Err(devgraph_client_native::NativeError::new(
                        "control_limit",
                        false,
                    ));
                }
                let host = host.clone();
                let send = send.clone();
                controls.spawn(async move {
                    let response = host.handle(&raw).await;
                    let _ = send.send(response).await;
                });
            }
            Ok(None) => break Ok(()),
            Err(error) => break Err(error),
        }
    };
    host.disconnect().await;
    controls.abort_all();
    while controls.join_next().await.is_some() {}
    drop(send);
    if !writer_finished {
        writer.abort();
        let _ = writer.await;
    }
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[tokio::test]
    async fn framing_bounds_prefixes_partial_frames_and_round_trips() {
        assert!(read_frame(&mut &b""[..]).await.unwrap().is_none());
        for bytes in [
            vec![1],
            0u32.to_ne_bytes().to_vec(),
            ((FRAME_BYTES + 1) as u32).to_ne_bytes().to_vec(),
            vec![2, 0, 0, 0, b'{'],
        ] {
            assert_eq!(
                read_frame(&mut bytes.as_slice()).await.unwrap_err().code,
                "invalid_frame"
            );
        }
        let value = json!({"v":1,"id":"frame","ok":true,"result":{"text":"é"}});
        let mut bytes = Vec::new();
        write_frame(&mut bytes, &value).await.unwrap();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(
                &read_frame(&mut bytes.as_slice()).await.unwrap().unwrap()
            )
            .unwrap(),
            value
        );
        let oversized = json!({"value":"x".repeat(FRAME_BYTES)});
        assert_eq!(
            write_frame(&mut Vec::new(), &oversized)
                .await
                .unwrap_err()
                .code,
            "invalid_frame"
        );
    }
    #[tokio::test]
    async fn blocked_output_and_control_flood_close_port_with_bounded_tasks() {
        let host = host::fixture_host();
        let mut input = Vec::new();
        for n in 0..1000 {
            write_frame(&mut input, &json!({"v":1,"id":format!("flood-{n}"),"action":"cancel","connection_id":"missing","target_id":"target"})).await.unwrap();
        }
        let (output, _unread) = tokio::io::duplex(1);
        let result = tokio::time::timeout(
            std::time::Duration::from_secs(2),
            serve(host, input.as_slice(), output),
        )
        .await
        .unwrap();
        assert_eq!(result.unwrap_err().code, "control_limit");
    }
}
