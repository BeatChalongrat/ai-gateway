// Compile the .proto into Rust types via tonic-build. The generated
// code lands in OUT_DIR and is pulled in by
// ``tonic::include_proto!("dlp")`` from src/main.rs.

fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        .compile(&["proto/dlp.proto"], &["proto"])?;
    Ok(())
}
