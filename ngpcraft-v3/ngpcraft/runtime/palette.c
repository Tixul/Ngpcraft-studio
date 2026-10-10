void ngc_palette(unsigned char slot, const unsigned short *colors)
{
    volatile unsigned short *dest;
    unsigned char i;
    dest = (volatile unsigned short *)0x8280 + ((unsigned short)slot * 4u);
    for (i = 0u; i < 4u; ++i) dest[i] = colors[i];
}
