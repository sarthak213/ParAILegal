// ParAILegal desktop shell (docs/PACKAGING.md, step 5).
//
// Starts the bundled backend (resources/backend/parailegal-backend.exe, backend/desktop_server.py)
// on a free local port, tells the page where it is, and stops it on exit:
//
//   - the backend gets the install's resources folder (the law, search models, model engine) and the
//     user's data folder, %LOCALAPPDATA%\ParAILegal (the downloads, logs)
//   - it is given this process's id and exits by itself if the shell is gone (a crash, Task
//     Manager), and the model server goes with it (its job object): nothing is left running
//   - the page learns the backend's address from window.__PARAILEGAL_API__, set before it loads

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Backend(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

fn start_backend(resources: &PathBuf, home: &PathBuf, port: u16) -> std::io::Result<Child> {
    let exe = resources.join("backend").join("parailegal-backend.exe");
    let mut cmd = Command::new(exe);
    cmd.arg("--resources")
        .arg(resources)
        .arg("--home")
        .arg(home)
        .arg("--port")
        .arg(port.to_string())
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window behind the app
    }
    cmd.spawn()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let resources = app.path().resource_dir()?.join("resources");
            let home = app.path().local_data_dir()?.join("ParAILegal");
            std::fs::create_dir_all(&home)?;
            let port = free_port()?;
            let child = start_backend(&resources, &home, port)?;
            app.manage(Backend(Mutex::new(Some(child))));

            let script = format!("window.__PARAILEGAL_API__ = 'http://127.0.0.1:{port}';");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("ParAILegal")
                .inner_size(1400.0, 900.0)
                .min_inner_size(900.0, 600.0)
                .initialization_script(&script)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("ParAILegal could not start")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
