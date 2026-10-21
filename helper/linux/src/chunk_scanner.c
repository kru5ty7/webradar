/*
 * chunk_scanner.c — fast entity-chunk locator for CS2 Linux
 *
 * Scans a raw memory buffer for a CEntityIdentity array (chunk-0).
 *
 * Strategy: CS2 initialises ALL 512 entity slots at startup with sequential
 * m_Idx values (low 15 bits = slot index) regardless of whether entities are
 * present.  Active slots additionally have serial > 0 (bits 31..15).
 *
 * A valid chunk satisfies all of:
 *   1. min_slots consecutive slots all have (m_Idx & 0x7FFF) == slot_number
 *      (serial may be 0 — works even in the CS2 main menu)
 *   2. At least one of those slots has a valid 0x7F... heap pointer at
 *      offset 0 of its CEntityIdentity (m_pObject — the entity pointer),
 *      confirming this is a live entity array.
 *
 * Using min_slots=32 makes accidental matches from other data structures
 * effectively impossible (32 uint32 values at stride-N intervals with exactly
 * sequential low-15-bit values at a specific inner offset).
 *
 * Compile:
 *   gcc -O2 -shared -fPIC -o chunk_scanner.so chunk_scanner.c
 */

#include <stdint.h>
#include <stddef.h>

static inline uint32_t read_u32(const uint8_t *p)
{
    uint32_t v;
    __builtin_memcpy(&v, p, 4);
    return v;
}

static inline uint64_t read_u64(const uint8_t *p)
{
    uint64_t v;
    __builtin_memcpy(&v, p, 8);
    return v;
}

/*
 * scan_for_chunk
 *   buf       : raw bytes of the memory region
 *   n         : byte count
 *   stride    : bytes per CEntityIdentity (typically 112, 120, or 128)
 *   idx_off   : byte offset of m_Idx within each identity (typically 0x10)
 *   min_slots : consecutive sequential-index slots required (recommend 32)
 *
 * Returns the byte offset from buf[] of the chunk start (slot-0 identity),
 * or -1 if not found.
 */
int64_t scan_for_chunk(const uint8_t *buf, size_t n,
                       int stride, int idx_off, int min_slots)
{
    if (!buf)
        return -1;
    /* Need room for min_slots entries + idx_off */
    size_t need = (size_t)(min_slots * stride) + (size_t)idx_off + 4;
    if (n < need)
        return -1;

    size_t end = n - need;

    for (size_t chunk_base = 0; chunk_base <= end; chunk_base += 4) {

        /* Check 1 (fast reject): slot-0 m_Idx low 15 bits must be 0 */
        uint32_t v0 = read_u32(buf + chunk_base + idx_off);
        if ((v0 & 0x7FFF) != 0)
            goto next;

        /* Check 2: min_slots consecutive slots with (m_Idx & 0x7FFF) == slot.
         * Serial (bits 31..15) may be 0 for uninitialised/empty slots. */
        {
            int ok = 1;
            for (int slot = 1; slot < min_slots; slot++) {
                size_t p = chunk_base + (size_t)(slot * stride) + idx_off;
                uint32_t vi = read_u32(buf + p);
                if ((vi & 0x7FFF) != (uint32_t)slot) {
                    ok = 0;
                    break;
                }
            }
            if (!ok)
                goto next;
        }

        /* Check 3: at least one slot in [0, min_slots) must have serial > 0
         * (bits 31..15 of m_Idx, i.e. the slot is occupied) AND a valid
         * 0x7F... heap pointer at offset 0 (m_pObject).
         * The world entity always occupies slot 0 with serial >= 1.
         * Pre-allocated pools or false positives typically have all serial=0. */
        {
            int has_active = 0;
            for (int slot = 0; slot < min_slots; slot++) {
                size_t idx_p = chunk_base + (size_t)(slot * stride) + (size_t)idx_off;
                uint32_t vi = read_u32(buf + idx_p);
                if ((vi >> 15) == 0)
                    continue;  /* serial = 0, not an active entity slot */
                size_t obj_p = chunk_base + (size_t)(slot * stride);
                if (obj_p + 8 > n)
                    break;
                uint64_t ptr = read_u64(buf + obj_p);
                if (ptr >= 0x7F0000000000ULL && ptr <= 0x7FFFFFFFFFFFULL) {
                    has_active = 1;
                    break;
                }
            }
            if (!has_active)
                goto next;
        }

        return (int64_t)chunk_base;

    next:;
    }
    return -1;
}
