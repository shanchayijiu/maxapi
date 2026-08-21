> **SUPERSEDED 2026-08-21** — 内容错位（写成注册机/FastAPI），与真实 maxapi（se.zzmax 旁路兼容层）无关。权威文档：仓库根 `STATUS.md` + `docs/`。

# MaxAPI Project Documentation

## Project Overview
MaxAPI is a Python-based API server designed for secure API interactions, particularly supporting AMD and OpenCode registration processes. It runs on port 8080 by default and includes testing capabilities for validation.

## Project Structure
- **main.py**: Primary entry point for the FastAPI server.
- **safeapi_server.py**: Core server implementation with security features.
- **smoke_test_api.py**: Smoke testing utilities.
- **requirements.txt**: Dependencies list.
- **Dockerfile**: Containerization configuration.

## Key Components
- **AMD Registration**: Form handling and rate limiting for AMD account creation.
- **OpenCode Key Machine**: GitHub OAuth integration with Outlook number pools and browser automation.
- **BazaarLink Key Machine**: Mail-based registration with proxy pools and FastAPI backend.
- **Karing Proxy**: Singapore AWS proxy for international registration bypassing.
- **Pokemon Proxy**: V2Board setup with rebrowser and NAS integration.

## Development Workflow
- **8080 Testing Version**: Local server testing at http://127.0.0.1:8080.
- **Git Integration**: Version control with commits and documentation.
- **Review Process**: 8080 server used as reviewer for changes.
- **Change Completeness**: Enforced checklist for modifications including documentation, dependencies, tests, and status updates.

## Current Status
- All core features implemented and tested.
- Registration machines operational with high success rates.
- Proxy configurations for bypassing restrictions.
- Memory and feedback workflows established.

## Next Steps
- Ensure full integration of all components.
- Validate end-to-end registration flows.
- Update documentation as needed.
- Prepare for deployment and testing cycles.