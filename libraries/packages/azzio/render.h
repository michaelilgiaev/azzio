/* Azzio bare-`azzio` terminal user interface (C) -- the renderer (centred, coloured ANSI drawing).
 *
 * Pure terminal drawing over RAW ANSI (no ncurses): everything is CENTRED, the accent is
 * the logo cyan, the navigation keys are UPPERCASED and coloured. The renderer owns a
 * back buffer it fills each frame and flushes in one write(), so redraws don't flicker
 * and a keystroke repaints instantly.
 *
 * It draws TEXT only. The kitty image previews are placed separately (preview.c) into a
 * rectangle the renderer reserves and reports via az_render_preview_rect(), so the two
 * never fight over the same cells.
 */
#ifndef AZ_RENDER_H
#define AZ_RENDER_H

#include "terminal_user_interface.h"

/* What has keyboard focus / what the UI is doing right now. Browsing is the normal menu;
 * SEARCH is the top box; PORT/PROMPT/PASSWORD are the in-UI text prompts an apply can raise
 * (PORT = a port number; PROMPT = free text like a path/remote for an AZ_ACT_PROMPT row;
 * PASSWORD = the masked sudo password); OUTPUT is the results overlay shown after an apply
 * (esp. a "list ports"). All of these are drawn INSIDE the alt screen -- there is no dropping
 * to the real terminal anymore. */
typedef enum {
    AZ_MODE_BROWSE = 0,
    AZ_MODE_SEARCH,
    AZ_MODE_PORT,       /* typing a port number for an AZ_ACT_PORT row              */
    AZ_MODE_PROMPT,     /* typing free text (path / remote) for an AZ_ACT_PROMPT row */
    AZ_MODE_PASSWORD,   /* typing the sudo password (masked) before an apply         */
    AZ_MODE_OUTPUT,     /* showing an apply's captured output in the overlay         */
} AzMode;

/* The interactive UI state the renderer draws (owned by main.c). */
typedef struct {
    const char *stack[16];  /* screen-id breadcrumb; stack[0]=="main" */
    int depth;              /* number of ids on the stack (>=1)       */
    int sel;                /* selected VISIBLE row index             */
    char query[128];        /* search box contents                    */
    int searching;          /* legacy flag: mode==AZ_MODE_SEARCH      */
    char message[256];      /* last action result (shown briefly)     */
    int rows, cols;         /* terminal size                          */

    AzMode mode;            /* current input mode (see AzMode)                     */
    char input[128];        /* the PORT / PASSWORD prompt's typed text             */
    const char *prompt;     /* label shown above the input line (e.g. "Port:")     */
    char *output;           /* captured apply output for the OUTPUT overlay (heap) */
    int output_scroll;      /* first visible line in the OUTPUT overlay            */
    char output_title[128]; /* the overlay's title (the command that produced it)  */
} AzUI;

/* A rectangle in terminal cells (1-based row/col, like ANSI). */
typedef struct { int row, col, w, h; int valid; } AzRect;

/* The visible rows on the current screen after the search filter. Fills `out` (capacity
 * cap) with pointers into the model and returns the count. */
int az_visible_rows(const AzUI *ui, const AzRow **out, int cap);

/* The current screen (top of the stack). */
const AzScreen *az_ui_screen(const AzUI *ui);

/* Draw the whole frame for `ui` into an internal buffer and flush it to stdout. If
 * `preview_out` is non-NULL it receives the rectangle reserved for the hovered row's
 * preview (valid==0 when the row has none), so the caller can place the kitty image.
 *
 * When ui->mode is PORT/PASSWORD/OUTPUT this draws the corresponding centred overlay INSTEAD
 * of the menu (and reports no preview rect), so an apply's prompt/result stays inside the UI.
 * The menu is drawn for BROWSE and SEARCH. */
void az_render(const AzUI *ui, AzRect *preview_out);

/* How many text lines the OUTPUT overlay can show at the current terminal height -- main.c
 * uses this to clamp ui->output_scroll when paging a long "list ports" result. */
int az_output_page_rows(const AzUI *ui);
/* Number of lines in ui->output (0 if none). */
int az_output_line_count(const AzUI *ui);

/* The bottom navigation line, as a plain (uncoloured) string -- used by tests to assert
 * the keys are advertised. Writes into buf (size n), returns buf. */
const char *az_nav_plain(char *buf, size_t n);

#endif /* AZ_RENDER_H */
