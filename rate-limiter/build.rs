// Compiles the .proto into Rust types via tonic-build. Runs every
// build; the generated code lands in ``OUT_DIR`` and is included via
// ``tonic::include_proto!("ratelimit")`` inside src/main.rs.

fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        .compile(&["proto/ratelimit.proto"], &["proto"])?;
    Ok(())
}
