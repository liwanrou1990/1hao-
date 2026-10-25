// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Safe counterpart to MiniDAO using different naming than the X100 case study:
// snapshot voting, timelock, allowlisted+capped treasury bridge, TWAP pricing.
// Expected detector output: ZERO findings (negative benchmark sample).
interface ISnapshotToken {
    function getPastVotes(address, uint256) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}
interface ITwapFeed { function consult() external view returns (uint256); }

contract SafeMiniDAO {
    ISnapshotToken public voteToken;
    address public owner;
    bool private _bootstrapped;
    mapping(address => bool) public stewards;

    struct Ballot { address target; bytes payload; uint256 yes; uint256 snapshotBlock;
                    uint64 closes; uint64 eta; bool queued; bool done; }
    mapping(uint256 => Ballot) public ballots;
    uint256 public ballotCount;
    uint256 public votingWindow = 3 days;
    uint256 public constant MIN_DELAY = 2 days;

    constructor(ISnapshotToken t) { voteToken = t; owner = msg.sender; }
    modifier onlyOwner() { require(msg.sender == owner, "not owner"); _; }

    function configureStewards(address[] calldata who) external onlyOwner {
        require(!_bootstrapped, "init");
        for (uint256 i; i < who.length; i++) stewards[who[i]] = true;
        _bootstrapped = true;
    }

    function submit(address target, bytes calldata payload) external returns (uint256 id) {
        id = ++ballotCount;
        Ballot storage b = ballots[id];
        b.target = target; b.payload = payload;
        b.snapshotBlock = block.number - 1;
        b.closes = uint64(block.timestamp) + uint64(votingWindow);
    }

    function castBallot(uint256 id, bool yes) external {
        Ballot storage b = ballots[id];
        require(block.timestamp <= b.closes, "closed");
        uint256 w = voteToken.getPastVotes(msg.sender, b.snapshotBlock); // snapshot
        if (yes) b.yes += w;
    }

    function queue(uint256 id) external {
        Ballot storage b = ballots[id];
        require(block.timestamp > b.closes, "open");
        require(b.yes >= voteToken.totalSupply() / 5, "quorum");
        b.queued = true;
        b.eta = uint64(block.timestamp + MIN_DELAY); // timelock
    }

    function enact(uint256 id) external returns (bytes memory) {
        Ballot storage b = ballots[id];
        require(b.queued && !b.done, "state");
        require(block.timestamp >= b.eta, "timelock");
        b.done = true;
        (bool ok, bytes memory ret) = b.target.call(b.payload);
        require(ok, "fail");
        return ret;
    }
}

contract SafeLendingDesk {
    ITwapFeed public feed;
    mapping(address => uint256) public deposits;
    constructor(ITwapFeed f) { feed = f; }
    function deposit() external payable { deposits[msg.sender] += msg.value; }
    function collateralValue(address u) public view returns (uint256) {
        uint256 price = feed.consult(); // TWAP, not spot
        return (deposits[u] * price) / 1e18;
    }
}
