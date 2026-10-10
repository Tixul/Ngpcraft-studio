/* BIOS cartridge header; this object MUST be linked first. */
extern void main(void);
const char ngc_license[28] = " LICENSED BY SNK CORPORATION";
void (* const ngc_entry)(void) = main;
const unsigned short ngc_cart_id = 0u;
const unsigned short ngc_cart_system = 0x1000u;
const char ngc_title[12] = "NGPCRAFT V3 ";
const unsigned long ngc_reserved[4] = {0UL, 0UL, 0UL, 0UL};
