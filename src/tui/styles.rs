//! TUI theme and styling

use ratatui::style::Color;

#[derive(Debug, Clone)]
pub struct Theme {
    // Background and borders
    pub background: Color,
    pub border: Color,
    pub terminal_border: Color,
    pub selection: Color,
    pub session_selection: Color,

    // Text colors
    pub title: Color,
    pub text: Color,
    pub dimmed: Color,
    pub hint: Color,

    // Status colors
    pub running: Color,
    pub waiting: Color,
    pub idle: Color,
    pub error: Color,
    pub terminal_active: Color,

    // UI elements
    pub group: Color,
    pub search: Color,
    pub accent: Color,
}

impl Default for Theme {
    fn default() -> Self {
        Self::kisti()
    }
}

impl Theme {
    pub fn kisti() -> Self {
        Self {
            background: Color::Rgb(8, 22, 37),
            border: Color::Rgb(0, 84, 132),
            terminal_border: Color::Rgb(0, 117, 186),
            selection: Color::Rgb(14, 39, 62),
            session_selection: Color::Rgb(36, 50, 65),

            title: Color::Rgb(56, 189, 248),
            text: Color::Rgb(200, 232, 248),
            dimmed: Color::Rgb(112, 142, 162),
            hint: Color::Rgb(136, 176, 198),

            running: Color::Rgb(34, 211, 238),
            waiting: Color::Rgb(218, 33, 40),
            idle: Color::Rgb(86, 118, 138),
            error: Color::Rgb(255, 100, 80),
            terminal_active: Color::Rgb(130, 170, 255),

            group: Color::Rgb(125, 211, 252),
            search: Color::Rgb(186, 230, 253),
            accent: Color::Rgb(0, 117, 186),
        }
    }
}
