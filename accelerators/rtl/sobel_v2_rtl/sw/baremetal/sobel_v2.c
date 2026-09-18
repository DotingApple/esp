/* Copyright (c) 2011-2024 Columbia University, System Level Design Group */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdio.h>
#ifndef __riscv
#include <stdlib.h>
#endif

#include <esp_accelerator.h>
#include <esp_probe.h>
#include <fixed_point.h>
/* ESP_MON_INSTRUMENTED BEGIN */
/* --- ESP_MON_INSTRUMENTED ---
 * Monitor and cycle reporting, added by setup/instrument_baremetal.py.
 * The counters are memory-mapped registers; enabling them in .esp_config
 * instantiates the hardware, but only this code makes them observable.
 */
#include <monitors.h>
/* Tile coordinates, generated per SoC. libmonitors.c includes this inside
 * esp_monitor() so the arrays are locals there; including it here gives this
 * file its own copies at file scope, which is what the tile-index computations
 * below need. */
#include "soc_locs.h"

/* Read one monitor. READ_SINGLE returns the value directly and ignores the
 * vals pointer, so NULL is what the guide's Example 1 passes. */
#define __MON_RD(tile, idx, dst)          \
	do {                                     \
		__mon_args.tile_index = (tile);        \
		__mon_args.mon_index  = (idx);         \
		(dst) = esp_monitor(__mon_args, NULL); \
	} while (0)

/* sub_monitor_vals accounts for counter overflow between the two reads. */
#define __MON_PR(name, s, e) \
	printf("ESP_MON %s %u\n", (name), (unsigned) sub_monitor_vals((s), (e)))

/* Reads mcycle, the machine-mode cycle counter, NOT rdcycle.
 *
 * rdcycle is the pseudo-instruction for the user-mode `cycle` CSR (0xC00), and
 * this Ariane configuration traps on it: an instrumented run died on that one
 * instruction while printing nothing further, which the testbench reported as
 * "Program Completed!" because top.vhd asserts on cpuerr. It cost three
 * full simulations to find, because the fault looked like an esp_monitor
 * problem -- esp_monitor sits next to it and is entirely innocent.
 *
 * mcycle (0xB00) is implemented and readable here; the program runs in machine
 * mode, which the CPU trace confirms.
 */
static inline uint64_t esp_read_cycles(void)
{
#ifdef __riscv
	uint64_t __c;
	__asm__ volatile ("csrr %0, mcycle" : "=r" (__c));
	return __c;
#else
	return 0;
#endif
}
/* ESP_MON_INSTRUMENTED END */

typedef int64_t token_t;

static unsigned DMA_WORD_PER_BEAT(unsigned _st)
{
        return (sizeof(void *) / _st);
}


#define SLD_SOBEL_V2 0x014
#define DEV_NAME "sld,sobel_v2_rtl"

/* <<--params-->> */
const int32_t width = 10;
const int32_t height = 10;

static unsigned in_words_adj;
static unsigned out_words_adj;
static unsigned in_len;
static unsigned out_len;
static unsigned in_size;
static unsigned out_size;
static unsigned out_offset;
static unsigned mem_size;
unsigned beats          = (width * height + 7) / 8; 
/* Size of the contiguous chunks for scatter/gather */
#define CHUNK_SHIFT 20
#define CHUNK_SIZE BIT(CHUNK_SHIFT)
#define NCHUNK(_sz) ((_sz % CHUNK_SIZE == 0) ?		\
			(_sz / CHUNK_SIZE) :		\
			(_sz / CHUNK_SIZE) + 1)

/* User defined registers */
/* <<--regs-->> */
#define SOBEL_V2_WIDTH_REG 0x44
#define SOBEL_V2_HEIGHT_REG 0x40


static int validate_buf(token_t *out, token_t *gold)
{
	int i;
	int j;
	unsigned errors = 0;

	for (i = 0; i < 1; i++)
		for (j = 0; j < width*height; j++)
			if (gold[i * out_words_adj + j] != out[i * out_words_adj + j])
				errors++;

	return errors;
}


static void init_buf (token_t *in, token_t * gold)
{
	int i;
	int j;

	in[0] = 0x0102030405060708ULL; // first pixel = 1, second = 2, ..., eighth = 9
	in[1] = 0x1011121314151617ULL; // first pixel = 10, second = 11, ..., eighth = 18
	in[2] = 0x2021222324252627ULL; // first pixel = 20, second = 21, ..., eighth = 28
	in[3] = 0x3031323334353637ULL; // first pixel = 30, second = 31, ..., eighth = 38
	in[4] = 0x4041424344454647ULL; // first pixel = 40, second = 41, ..., eighth = 48
	in[5] = 0x0101020304050607ULL; // first pixel = 1, second = 2, ..., eighth = 8
	in[6] = 0x0203040506070809ULL; // first pixel = 2, second = 3, ..., eighth = 10
	in[7] = 0x030405060708090aULL; // first pixel = 3, second = 4, ..., eighth = 11
	in[8] = 0x0405060708090a0bULL; // first pixel = 4, second = 5, ..., eighth = 12
	in[9] = 0x05060708090a0b0cULL; // first pixel = 5, second = 6, ..., eighth = 13
	in[10] = 0x060708090a0b0c0dULL; // first pixel = 6, second = 7, ..., eighth = 14
	in[11] = 0x0708090a0b0c0d0eULL; // first pixel = 7, second = 8, ..., eighth = 15
	in[12] = 0x08090a0b0c0d0e0fULL; // first pixel = 8, second = 9, ..., eighth = 16
	in[13] = 0x090a0b0c0d0e0f10ULL; // first pixel = 9, second = 10, ..., eighth = 17
	in[14] = 0x0a0b0c0d0e0f1011ULL; // first pixel = 10, second = 11, ..., eighth = 18

}


int main(int argc, char * argv[])
{
	int i;
	int n;
	int ndev;
	struct esp_device *espdevs;
	struct esp_device *dev;
	unsigned done;
	unsigned **ptable;
	token_t *mem;
	token_t *gold;
	unsigned errors = 0;
	unsigned coherence;

	in_words_adj            = round_up(beats, DMA_WORD_PER_BEAT(sizeof(token_t)));
	out_words_adj           = in_words_adj;
	
	in_len                  = in_words_adj;                    // now “beats”
	out_len                 = out_words_adj;
	in_size                 = in_len  * sizeof(token_t);       // bytes
	out_size                = out_len * sizeof(token_t);
	out_offset              = in_len;                          // output after input
	mem_size                = (in_len + out_len) * sizeof(token_t);
	

	// Search for the device
	printf("Scanning device tree... \n");

	ndev = probe(&espdevs, VENDOR_SLD, SLD_SOBEL_V2, DEV_NAME);
	if (ndev == 0) {
		printf("sobel_v2 not found\n");
		return 0;
	}

	for (n = 0; n < ndev; n++) {

		printf("**************** %s.%d ****************\n", DEV_NAME, n);

		dev = &espdevs[n];

		// Check DMA capabilities
		if (ioread32(dev, PT_NCHUNK_MAX_REG) == 0) {
			printf("  -> scatter-gather DMA is disabled. Abort.\n");
			return 0;
		}

		if (ioread32(dev, PT_NCHUNK_MAX_REG) < NCHUNK(mem_size)) {
			printf("  -> Not enough TLB entries available. Abort.\n");
			return 0;
		}

		// Allocate memory
		gold = aligned_malloc(out_size);
		mem = aligned_malloc(mem_size);
		printf("  memory buffer base-address = %p\n", mem);

		// Alocate and populate page table
		ptable = aligned_malloc(NCHUNK(mem_size) * sizeof(unsigned *));
		for (i = 0; i < NCHUNK(mem_size); i++)
			ptable[i] = (unsigned *) &mem[i * (CHUNK_SIZE / sizeof(token_t))];

		printf("  ptable = %p\n", ptable);
		printf("  nchunk = %lu\n", NCHUNK(mem_size));

#ifndef __riscv
		for (coherence = ACC_COH_NONE; coherence <= ACC_COH_RECALL; coherence++) {
#else
		{
			/* TODO: Restore full test once ESP caches are integrated */
			coherence = ACC_COH_NONE;
#endif
			printf("  --------------------\n");
			printf("  Generate input...\n");
			init_buf(mem, gold);

			// Pass common configuration parameters

			// iowrite32(dev, SELECT_REG, ioread32(dev, DEVID_REG));
			iowrite32(dev, COHERENCE_REG, coherence);

#ifndef __sparc
			iowrite32(dev, PT_ADDRESS_REG, (unsigned long long) ptable);
#else
			iowrite32(dev, PT_ADDRESS_REG, (unsigned) ptable);
#endif
			iowrite32(dev, PT_NCHUNK_REG, NCHUNK(mem_size));
			iowrite32(dev, PT_SHIFT_REG, CHUNK_SHIFT);

			// Use the following if input and output data are not allocated at the default offsets
			iowrite32(dev, SRC_OFFSET_REG, 0x0);
			iowrite32(dev, DST_OFFSET_REG, 0x0);

			// Pass accelerator-specific configuration parameters
			/* <<--regs-config-->> */
		iowrite32(dev, SOBEL_V2_WIDTH_REG, width);
		iowrite32(dev, SOBEL_V2_HEIGHT_REG, height);

			// Flush (customize coherence model here)
/* ESP_MON_INSTRUMENTED BEGIN */
			/* ESP_MON_INSTRUMENTED: capture before the accelerator starts */
			esp_monitor_args_t __mon_args;
			unsigned int __acc_tot_s, __acc_tot_e, __acc_mem_s, __acc_mem_e;
			unsigned int __acc_tlb_s, __acc_tlb_e, __acc_inv_s, __acc_inv_e;
			unsigned int __ddr_s, __ddr_e, __llc_h_s, __llc_h_e, __llc_m_s, __llc_m_e;
			unsigned int __l2_h_s, __l2_h_e, __l2_m_s, __l2_m_e;
			const int __acc_tile = acc_locs[0].row * SOC_COLS + acc_locs[0].col;
			const int __mem_tile = mem_locs[0].row * SOC_COLS + mem_locs[0].col;
			const int __cpu_tile = cpu_locs[0].row * SOC_COLS + cpu_locs[0].col;
			uint64_t __cyc_start, __cyc_end;
			__mon_args.read_mode = ESP_MON_READ_SINGLE;
			__MON_RD(__acc_tile, MON_ACC_TOT_LO_INDEX, __acc_tot_s);
			__MON_RD(__acc_tile, MON_ACC_MEM_LO_INDEX, __acc_mem_s);
			__MON_RD(__acc_tile, MON_ACC_TLB_INDEX, __acc_tlb_s);
			__MON_RD(__acc_tile, MON_ACC_INVOCATIONS, __acc_inv_s);
			__MON_RD(__mem_tile, MON_DDR_WORD_TRANSFER_INDEX, __ddr_s);
			__MON_RD(__mem_tile, MON_LLC_HIT_INDEX, __llc_h_s);
			__MON_RD(__mem_tile, MON_LLC_MISS_INDEX, __llc_m_s);
			__MON_RD(__cpu_tile, MON_L2_HIT_INDEX, __l2_h_s);
			__MON_RD(__cpu_tile, MON_L2_MISS_INDEX, __l2_m_s);
			__cyc_start = esp_read_cycles();
			/* ESP_MON_INSTRUMENTED END */
			esp_flush(coherence);

			// Start accelerators
			printf("  Start...\n");
			iowrite32(dev, CMD_REG, CMD_MASK_START);

			// Wait for completion
			done = 0;
			while (!done) {
				done = ioread32(dev, STATUS_REG);
				done &= STATUS_MASK_DONE;
			}
			iowrite32(dev, CMD_REG, 0x0);
			/* ESP_MON_INSTRUMENTED BEGIN */
			/* ESP_MON_INSTRUMENTED: capture after the accelerator reports done */
			__cyc_end = esp_read_cycles();
			__MON_RD(__acc_tile, MON_ACC_TOT_LO_INDEX, __acc_tot_e);
			__MON_RD(__acc_tile, MON_ACC_MEM_LO_INDEX, __acc_mem_e);
			__MON_RD(__acc_tile, MON_ACC_TLB_INDEX, __acc_tlb_e);
			__MON_RD(__acc_tile, MON_ACC_INVOCATIONS, __acc_inv_e);
			__MON_RD(__mem_tile, MON_DDR_WORD_TRANSFER_INDEX, __ddr_e);
			__MON_RD(__mem_tile, MON_LLC_HIT_INDEX, __llc_h_e);
			__MON_RD(__mem_tile, MON_LLC_MISS_INDEX, __llc_m_e);
			__MON_RD(__cpu_tile, MON_L2_HIT_INDEX, __l2_h_e);
			__MON_RD(__cpu_tile, MON_L2_MISS_INDEX, __l2_m_e);
			printf("ESP_MON_BEGIN\n");
			printf("ESP_CPU_CYCLES %llu\n", (unsigned long long)(__cyc_end - __cyc_start));
			__MON_PR("acc_total_cycles", __acc_tot_s, __acc_tot_e);
			__MON_PR("acc_mem_cycles",   __acc_mem_s, __acc_mem_e);
			__MON_PR("acc_tlb_cycles",   __acc_tlb_s, __acc_tlb_e);
			__MON_PR("acc_invocations",  __acc_inv_s, __acc_inv_e);
			__MON_PR("ddr_accesses",     __ddr_s,     __ddr_e);
			__MON_PR("llc_hits",         __llc_h_s,   __llc_h_e);
			__MON_PR("llc_misses",       __llc_m_s,   __llc_m_e);
			__MON_PR("l2_hits",          __l2_h_s,    __l2_h_e);
			__MON_PR("l2_misses",        __l2_m_s,    __l2_m_e);
			printf("ESP_MON_END\n");
			/* ESP_MON_INSTRUMENTED END */

			printf("  Done\n");
			printf("  validating...\n");

			/* Validation */
			errors = validate_buf(&mem[out_offset], gold);
			if (errors)
				printf("  ... FAIL\n");
			else
				printf("  ... PASS\n");
		}
		aligned_free(ptable);
		aligned_free(mem);
		aligned_free(gold);
	}

	return 0;
}
