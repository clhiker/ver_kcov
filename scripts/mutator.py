import os
import re
import random
import subprocess
import shutil

# BPF Jump instructions mapping for flipping
JMP_FLIPS = {
    'jeq': 'jne', 'jne': 'jeq',
    'jgt': 'jle', 'jle': 'jgt',
    'jge': 'jlt', 'jlt': 'jge',
    'jsgt': 'jsle', 'jsle': 'jsgt',
    'jsge': 'jslt', 'jslt': 'jsge'
}

def is_instruction(line):
    line = line.strip()
    if not line or line.startswith('.') or line.startswith('//') or line.endswith(':'):
        return False
    return True

def mutate_assembly(s_file, out_s_file):
    with open(s_file, 'r') as f:
        lines = f.readlines()
    
    mutated_lines = []
    mutations_applied = 0
    
    for line in lines:
        if not is_instruction(line):
            mutated_lines.append(line)
            continue
            
        original_line = line
        
        # Chance to mutate an instruction (e.g. 15% chance)
        if random.random() < 0.15:
            # 1. Try to flip jumps
            jump_match = re.search(r'\b(jeq|jne|jgt|jge|jlt|jle|jsgt|jsge|jslt|jsle)\b', line)
            if jump_match:
                op = jump_match.group(1)
                new_op = JMP_FLIPS.get(op, op)
                line = re.sub(r'\b' + op + r'\b', new_op, line, count=1)
                mutations_applied += 1
            else:
                # 2. Try to mutate immediates (avoiding registers like r1)
                # Look for standalone numbers, optionally preceded by = or + or -
                def repl_imm(m):
                    nonlocal mutations_applied
                    val = int(m.group(1))
                    choice = random.choice(['zero', 'flip_sign', 'add_1', 'sub_1', 'large_pos', 'large_neg', 'random'])
                    new_val = val
                    if choice == 'zero': new_val = 0
                    elif choice == 'flip_sign': new_val = -val
                    elif choice == 'add_1': new_val = val + 1
                    elif choice == 'sub_1': new_val = val - 1
                    elif choice == 'large_pos': new_val = 4096
                    elif choice == 'large_neg': new_val = -4096
                    elif choice == 'random': new_val = random.randint(-100, 100)
                    mutations_applied += 1
                    return str(new_val)
                
                # Regex for an immediate that isn't part of a register name or label
                # Match a space/equals/operator followed by the number
                line, num_subs = re.subn(r'(?<=[=+\-\s])([-+]?\d+)\b', repl_imm, line, count=1)
                    
        mutated_lines.append(line)
        
    with open(out_s_file, 'w') as f:
        f.writelines(mutated_lines)
        
    return mutations_applied

def generate_mutants(seed_c_file, mutants_count, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    base_name = os.path.basename(seed_c_file).split('.')[0]
    
    # 1. Compile seed to base assembly
    base_s = f"/tmp/{base_name}_base.s"
    cmd = ["clang", "-O2", "-g", "-target", "bpf", "-c", seed_c_file, "-S", "-o", base_s]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"[*] Base assembly generated for {seed_c_file}")
    
    success_count = 0
    for i in range(mutants_count):
        mutant_s = f"/tmp/{base_name}_mut_{i}.s"
        mutant_o = os.path.join(out_dir, f"{base_name}_mut_{i}.o")
        
        mut_count = mutate_assembly(base_s, mutant_s)
        
        if mut_count > 0:
            # Compile to object
            cmd = ["clang", "-O2", "-g", "-target", "bpf", "-c", mutant_s, "-o", mutant_o]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                success_count += 1
                
        # Clean up tmp assembly
        if os.path.exists(mutant_s): os.remove(mutant_s)
        
    if os.path.exists(base_s): os.remove(base_s)
    print(f"[+] Successfully generated {success_count} valid mutated .o files in {out_dir}/")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BPF Assembly Mutator")
    parser.add_argument("-i", "--input", required=True, help="Input seed .c file")
    parser.add_argument("-n", "--count", type=int, default=10, help="Number of mutants to generate")
    parser.add_argument("-o", "--output", required=True, help="Output directory for mutated .o files")
    
    args = parser.parse_args()
    generate_mutants(args.input, args.count, args.output)
