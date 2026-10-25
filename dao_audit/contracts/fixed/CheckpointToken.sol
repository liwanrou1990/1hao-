// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title CheckpointToken
/// @notice ERC20 with block-number vote checkpoints (ERC20Votes-style, minimal).
///         Used by SafeX100Governor so voting power is read from a PAST block,
///         which defeats same-transaction flash-loan vote manipulation.
///         LOCAL TEST CHAIN simulation only.
contract CheckpointToken {
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    struct Checkpoint { uint32 fromBlock; uint224 votes; }
    mapping(address => Checkpoint[]) private _checkpoints;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(string memory _name, string memory _symbol) {
        name = _name;
        symbol = _symbol;
    }

    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
        _writeCheckpoint(to, balanceOf[to]);
        emit Transfer(address(0), to, amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amount, "ERC20: allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "ERC20: balance");
        unchecked { balanceOf[from] -= amount; balanceOf[to] += amount; }
        _writeCheckpoint(from, balanceOf[from]);
        _writeCheckpoint(to, balanceOf[to]);
        emit Transfer(from, to, amount);
    }

    function _writeCheckpoint(address account, uint256 newVotes) internal {
        Checkpoint[] storage cps = _checkpoints[account];
        if (cps.length > 0 && cps[cps.length - 1].fromBlock == uint32(block.number)) {
            cps[cps.length - 1].votes = uint224(newVotes);
        } else {
            cps.push(Checkpoint({fromBlock: uint32(block.number), votes: uint224(newVotes)}));
        }
    }

    /// @notice Votes an account held at the end of `blockNumber` (must be in the past).
    function getPastVotes(address account, uint256 blockNumber) external view returns (uint256) {
        require(blockNumber < block.number, "votes: not yet determined");
        Checkpoint[] storage cps = _checkpoints[account];
        uint256 n = cps.length;
        if (n == 0) return 0;
        if (cps[n - 1].fromBlock <= blockNumber) return cps[n - 1].votes;
        if (cps[0].fromBlock > blockNumber) return 0;
        uint256 lo = 0;
        uint256 hi = n - 1;
        while (lo < hi) {
            uint256 mid = (lo + hi + 1) / 2;
            if (cps[mid].fromBlock <= blockNumber) lo = mid; else hi = mid - 1;
        }
        return cps[lo].votes;
    }
}
