/* Azzio application-menu UI scale -- the scale-1.0 DEFAULT (stock). This checked-in copy lets
 * the C tests compile at stock size; the ISO build (application_menu.build_daemon) OVERWRITES it
 * in its private build copy with the real GLOBAL_SCALE ratio generated from modifications/scale,
 * so the shipped menu's fixed-PIXEL geometry (theme.h) derives from the single scale source. The
 * menu's POINT fonts stay stock here and scale via the DPI channel (gtk-xft-dpi, from Xft.dpi)
 * -- see modifications/scale. Do NOT hand-edit the numbers: at scale 1.0 they are 100/100. */
#ifndef AZ_SCALE_H
#define AZ_SCALE_H
#define AZ_UI_SCALE_NUM 100
#define AZ_UI_SCALE_DEN 100
/* round(x * NUM / DEN) with integer math (the +DEN/2 rounds to nearest). */
#define AZ_SCALED(x) (((x) * AZ_UI_SCALE_NUM + AZ_UI_SCALE_DEN / 2) / AZ_UI_SCALE_DEN)
#endif /* AZ_SCALE_H */
